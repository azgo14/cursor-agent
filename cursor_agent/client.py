"""High-level Cursor ACP client."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from cursor_agent.session import (
    AcpStreamState,
    dispatch_acp_session_update,
)
from cursor_agent.transport import AcpTransport
from cursor_agent.types import CursorAgentHooks, PromptResult

log = logging.getLogger("cursor_agent.client")


async def _maybe_select_composer_fast(transport: AcpTransport, session_id: str) -> None:
    try:
        info = await transport.send_request("session/info", {"sessionId": session_id})
    except Exception:
        log.debug("session/info failed")
        return
    if not isinstance(info, dict):
        return
    opts = info.get("configOptions") or info.get("config_options")
    if not isinstance(opts, list):
        return

    model_opt: dict[str, Any] | None = None
    for opt in opts:
        if isinstance(opt, dict) and opt.get("id") == "model":
            model_opt = opt
            break
    if model_opt is None:
        return

    target = ""
    for optval in model_opt.get("options") or []:
        if not isinstance(optval, dict):
            continue
        blob = f"{optval.get('name', '')} {optval.get('value', '')}"
        if "composer" in blob.lower() and "fast" in blob.lower():
            target = str(optval.get("value", ""))
            break
    if not target:
        return

    try:
        await transport.send_request(
            "session/set_config_option",
            {"sessionId": session_id, "configId": "model", "value": target},
        )
    except Exception:
        log.debug("session/set_config_option failed")


StreamEvent = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class AcpSessionHandle:
    """Handle for a stable ``conversation_key`` with :class:`CursorAgent`."""

    agent: "CursorAgent"
    key: str

    async def prompt(
        self,
        text: str,
        *,
        on_event: Optional[StreamEvent] = None,
    ) -> PromptResult:
        return await self.agent.invoke_prompt(
            conversation_key=self.key,
            transcript=text,
            on_event=on_event,
        )

    async def prompt_stream(
        self,
        text: str,
    ) -> AsyncIterator[dict[str, Any]]:
        async for evt in self.agent.prompt_stream(text, conversation_key=self.key):
            yield evt


class CursorAgent:
    """One ACP subprocess (lazy-started) and per-conversation session mapping."""

    def __init__(
        self,
        cwd: Path,
        *,
        api_key: str | None = None,
        auth_token: str | None = None,
        client_name: str = "cursor-agent",
        client_version: str = "0.1.1",
        prefer_composer_fast: bool = True,
        hooks: CursorAgentHooks | None = None,
    ) -> None:
        self._cwd = cwd.resolve()
        self._prefer_composer_fast = prefer_composer_fast
        self._hooks = hooks or CursorAgentHooks()
        self._transport = AcpTransport(
            client_name=client_name,
            client_version=client_version,
            api_key=api_key,
            auth_token=auth_token,
            hooks=self._hooks,
        )
        self._conversation_to_session: dict[str, str] = {}
        self._compose_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()

    @property
    def transport(self) -> AcpTransport:
        """Low-level transport (for advanced callers)."""
        return self._transport

    def session(self, conversation_key: str) -> AcpSessionHandle:
        return AcpSessionHandle(agent=self, key=conversation_key)

    async def shutdown(self) -> None:
        await self._transport.shutdown()
        self._conversation_to_session.clear()

    async def prompt(
        self,
        text: str,
        *,
        conversation_key: str | None = None,
        on_event: Optional[StreamEvent] = None,
    ) -> PromptResult:
        key = conversation_key or str(uuid.uuid4())
        return await self.invoke_prompt(
            conversation_key=key,
            transcript=text,
            on_event=on_event,
        )

    async def prompt_stream(
        self,
        text: str,
        *,
        conversation_key: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        key = conversation_key or str(uuid.uuid4())
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def on_event(evt: dict[str, Any]) -> None:
            await queue.put(evt)

        outcome: list[PromptResult | BaseException | None] = [None]

        async def run_invoke() -> None:
            try:
                outcome[0] = await self.invoke_prompt(
                    conversation_key=key,
                    transcript=text,
                    on_event=on_event,
                )
            except BaseException as exc:
                outcome[0] = exc
            finally:
                await queue.put(None)

        task = asyncio.create_task(run_invoke())
        try:
            while True:
                evt = await queue.get()
                if evt is None:
                    break
                yield evt
            final = outcome[0]
            if isinstance(final, BaseException):
                raise final
            if isinstance(final, PromptResult):
                yield {
                    "event": "done",
                    "text": final.text,
                    "tool_rounds": final.completed_tool_rounds,
                }
        finally:
            await task

    async def invoke_prompt(
        self,
        *,
        conversation_key: str,
        transcript: str,
        on_event: Optional[StreamEvent] = None,
    ) -> PromptResult:
        """Run ``session/prompt`` for *transcript* in the ACP session for *conversation_key*."""
        acp_state = AcpStreamState(conversation_id=conversation_key)

        async def on_session_update(params: dict[str, Any]) -> None:
            await dispatch_acp_session_update(
                params,
                on_event=on_event,
                state=acp_state,
                hooks=self._hooks,
            )

        async with self._compose_lock:
            await self._transport.ensure_process(Path(self._cwd))

            prompt = [{"type": "text", "text": transcript}]
            session_id: str | None = None

            for attempt in range(2):
                async with self._session_lock:
                    sid = self._conversation_to_session.get(conversation_key)
                    if sid is None:
                        new_result: dict[str, Any] | None = None
                        for sn_try in range(2):
                            try:
                                new_result = await self._transport.send_request(
                                    "session/new",
                                    {
                                        "cwd": str(self._cwd),
                                        "mcpServers": [],
                                    },
                                )
                                break
                            except RuntimeError as e:
                                if sn_try == 0:
                                    log.warning(
                                        "cursor acp session/new failed; respawning agent",
                                        extra={"error": str(e)},
                                    )
                                    await self._transport.shutdown()
                                    await self._transport.ensure_process(Path(self._cwd))
                                else:
                                    raise
                        if new_result is None:
                            raise RuntimeError("session/new failed after retry")
                        if not isinstance(new_result, dict) or not new_result.get("sessionId"):
                            raise RuntimeError(f"session/new unexpected result: {new_result!r}")
                        sid = str(new_result["sessionId"])
                        if self._prefer_composer_fast:
                            await _maybe_select_composer_fast(self._transport, sid)
                        self._conversation_to_session[conversation_key] = sid
                    session_id = sid

                assert session_id is not None
                self._transport.set_session_update_handler(session_id, on_session_update)
                try:
                    await self._transport.send_request(
                        "session/prompt",
                        {"sessionId": session_id, "prompt": prompt},
                    )
                except RuntimeError as e:
                    self._transport.clear_session_update_handler(session_id)
                    self._conversation_to_session.pop(conversation_key, None)
                    if attempt == 0:
                        log.warning(
                            "cursor acp session/prompt failed; retrying with fresh ACP session",
                            extra={"error": str(e)},
                        )
                        continue
                    raise
                else:
                    self._transport.clear_session_update_handler(session_id)
                    break

        text = "".join(acp_state.accumulated_assistant_text).strip()
        return PromptResult(
            text=text or " ",
            completed_tool_rounds=list(acp_state.completed_tool_rounds),
        )
