"""JSON-RPC over stdio to ``agent acp`` (ACP transport)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

import shlex

from cursor_agent.auth import (
    build_agent_argv,
    cursor_cli_auth_configured,
    summarize_agent_argv_for_log,
)
from cursor_agent.types import CursorAgentHooks

log = logging.getLogger("cursor_agent.transport")


class AcpTransport:
    """One subprocess speaking ACP JSON-RPC over stdio."""

    def __init__(
        self,
        *,
        client_name: str = "cursor-agent",
        client_version: str = "0.1.1",
        api_key: str | None = None,
        auth_token: str | None = None,
        hooks: CursorAgentHooks | None = None,
    ) -> None:
        self._client_name = client_name
        self._client_version = client_version
        self._api_key = api_key
        self._auth_token = auth_token
        self._hooks = hooks or CursorAgentHooks()

        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._next_jsonrpc_id = 1
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._write_lock = asyncio.Lock()
        self._process_start_lock = asyncio.Lock()
        self._session_update_handlers: dict[str, Any] = {}

    @property
    def session_update_handlers(self) -> dict[str, Any]:
        return self._session_update_handlers

    def set_session_update_handler(self, session_id: str, handler: Any) -> None:
        self._session_update_handlers[session_id] = handler

    def clear_session_update_handler(self, session_id: str) -> None:
        self._session_update_handlers.pop(session_id, None)

    def _agent_argv(self, *parts: str) -> list[str]:
        return build_agent_argv(
            *parts,
            api_key=self._api_key,
            auth_token=self._auth_token,
        )

    def _initialize_params(self) -> dict[str, Any]:
        return {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False,
            },
            "clientInfo": {"name": self._client_name, "version": self._client_version},
        }

    async def _read_stderr(self, proc: asyncio.subprocess.Process) -> None:
        if proc.stderr is None:
            return
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            log.debug("cursor agent stderr: %s", line.decode(errors="replace").rstrip())

    async def _send_raw_result(self, req_id: int, result: Any) -> None:
        if self._process is None or self._process.stdin is None:
            return
        line = json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}) + "\n"
        async with self._write_lock:
            self._process.stdin.write(line.encode())
            await self._process.stdin.drain()

    async def _dispatch_line(self, msg: dict[str, Any]) -> None:
        if msg.get("jsonrpc") != "2.0":
            return

        mid = msg.get("id")

        if mid is not None and ("result" in msg or "error" in msg):
            fut = self._pending.pop(int(mid), None)  # type: ignore[arg-type]
            if fut is None:
                return
            if "error" in msg:
                err = msg["error"]
                if isinstance(err, dict):
                    parts = [
                        str(err.get("message", "error")),
                        f"code={err.get('code')!r}" if err.get("code") is not None else "",
                        f"data={err.get('data')!r}" if err.get("data") is not None else "",
                    ]
                    detail = " ".join(p for p in parts if p)
                    log.warning("cursor acp json-rpc error response", extra={"rpc_id": mid, "error": err})
                    fut.set_exception(RuntimeError(detail))
                else:
                    log.warning("cursor acp json-rpc error (non-object)", extra={"rpc_id": mid, "error": err})
                    fut.set_exception(RuntimeError(str(err)))
            else:
                fut.set_result(msg.get("result"))
            return

        method = msg.get("method")
        if method == "session/update":
            params = msg.get("params") or {}
            sid = params.get("sessionId") or params.get("session_id") or ""
            handler = self._session_update_handlers.get(sid)
            if handler is not None:
                await handler(params)
            return

        if method == "session/request_permission" and mid is not None:
            await self._send_raw_result(
                int(mid),
                {
                    "outcome": {
                        "outcome": "selected",
                        "optionId": "allow-once",
                    }
                },
            )

    async def _reader_loop(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        while True:
            line = await self._process.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line.decode())
            except json.JSONDecodeError:
                log.warning("cursor acp: non-json line: %r", line[:200])
                continue
            try:
                await self._dispatch_line(msg)
            except Exception:
                log.exception("cursor acp: handler error")

    async def send_request(self, method: str, params: dict[str, Any]) -> Any:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("Cursor ACP process is not running")

        async with self._write_lock:
            req_id = self._next_jsonrpc_id
            self._next_jsonrpc_id += 1
            loop = asyncio.get_event_loop()
            fut: asyncio.Future[Any] = loop.create_future()
            self._pending[req_id] = fut
            payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            self._process.stdin.write(json.dumps(payload).encode() + b"\n")
            await self._process.stdin.drain()

        try:
            return await fut
        except RuntimeError as e:
            raise RuntimeError(f"{method}: {e}") from e

    @staticmethod
    def _ensure_agent_on_path() -> None:
        local_bin = os.path.expanduser("~/.local/bin")
        path = os.environ.get("PATH", "")
        if local_bin not in path.split(os.pathsep):
            os.environ["PATH"] = local_bin + os.pathsep + path

    async def ensure_process(self, cwd: Path) -> None:
        async with self._process_start_lock:
            if self._process is not None and self._process.returncode is None:
                if self._reader_task is None:
                    log.warning("cursor acp process without reader; respawning")
                    await self.shutdown()
                elif self._reader_task.done():
                    exc = self._reader_task.exception()
                    log.warning(
                        "cursor acp reader ended while subprocess still running; respawning",
                        extra={"reader_exc": repr(exc) if exc else None},
                    )
                    await self.shutdown()
                else:
                    return

            if self._hooks.before_process_start is not None:
                await self._hooks.before_process_start(cwd)

            self._ensure_agent_on_path()

            if not cursor_cli_auth_configured(api_key=self._api_key, token=self._auth_token):
                log.warning(
                    "CURSOR_API_KEY / CURSOR_AUTH_TOKEN not set — Composer ACP calls may fail."
                )

            cmd_list = list(self._agent_argv("acp"))
            if not shutil.which(cmd_list[0]):
                raise ValueError(
                    f"Cursor CLI binary {cmd_list[0]!r} not found on PATH. "
                    "Install Cursor CLI and ensure `agent` is available (often ~/.local/bin)."
                )

            cwd_s = str(cwd.resolve())
            env = os.environ.copy()

            if self._hooks.wrap_command is not None:
                shell_cmd = await self._hooks.wrap_command(cmd_list, cwd_s)
            else:
                shell_cmd = shlex.join(cmd_list)

            self._process = await asyncio.create_subprocess_shell(
                shell_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd_s,
                env=env,
                limit=1024 * 1024,
            )
            self._next_jsonrpc_id = 1
            self._pending.clear()

            self._reader_task = asyncio.create_task(self._reader_loop())
            asyncio.create_task(self._read_stderr(self._process))

            await self.send_request("initialize", self._initialize_params())
            await self.send_request("authenticate", {"methodId": "cursor_login"})
            log.info(
                "Cursor ACP subprocess ready",
                extra={"cmd": summarize_agent_argv_for_log(cmd_list)},
            )

    async def shutdown(self) -> None:
        self._session_update_handlers.clear()
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self._process is not None:
            if self._process.returncode is None:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self._process.kill()
            self._process = None
        self._pending.clear()
