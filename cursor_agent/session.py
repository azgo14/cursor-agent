"""Map Cursor ACP ``session/update`` notifications to stream events / state."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from cursor_agent.types import CursorAgentHooks

log = logging.getLogger("cursor_agent.session")

StreamEventCallback = Optional[Callable[[dict[str, Any]], Awaitable[None]]]


@dataclass
class AcpStreamState:
    """Per-invoke state for ACP → app stream mapping."""

    conversation_id: str = ""
    accumulated_assistant_text: list[str] = field(default_factory=list)
    tool_display_names: dict[str, str] = field(default_factory=dict)
    tool_arguments: dict[str, dict[str, Any]] = field(default_factory=dict)
    completed_tool_call_ids: set[str] = field(default_factory=set)
    completed_tool_rounds: list[dict[str, Any]] = field(default_factory=list)
    mcp_opaque_ids: set[str] = field(default_factory=set)


def normalize_acp_tool_round(
    tid: str,
    name: str,
    args: dict[str, Any],
    result_s: str,
) -> dict[str, Any]:
    return {"id": tid, "name": name, "arguments": dict(args), "result": result_s}


def _extract_acp_chunk_text(upd: dict[str, Any]) -> Optional[str]:
    content = upd.get("content")
    if not isinstance(content, dict):
        return None
    t = content.get("text")
    if isinstance(t, str) and t:
        return t
    inner = content.get("content")
    if isinstance(inner, dict):
        t2 = inner.get("text")
        if isinstance(t2, str) and t2:
            return t2
    return None


def _tool_call_update_result_text(upd: dict[str, Any]) -> str:
    raw = upd.get("rawOutput")
    if raw is None:
        raw = upd.get("raw_output")
    if isinstance(raw, str) and raw.strip():
        return raw
    if isinstance(raw, (dict, list)):
        return json.dumps(raw, ensure_ascii=False)
    parts: list[str] = []
    for block in upd.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "content":
            c = block.get("content")
            if isinstance(c, dict) and isinstance(c.get("text"), str):
                parts.append(c["text"])
        else:
            c = block.get("content")
            if isinstance(c, dict) and isinstance(c.get("text"), str):
                parts.append(c["text"])
    return "\n".join(parts).strip()


_TERMINAL_TOOL_STATUSES = frozenset(
    {"completed", "complete", "failed", "error", "cancelled", "canceled"}
)


async def dispatch_acp_session_update(
    params: dict[str, Any],
    *,
    on_event: StreamEventCallback,
    state: AcpStreamState,
    hooks: CursorAgentHooks | None = None,
) -> None:
    """Translate one ACP ``session/update`` params dict into ``on_event`` calls."""
    upd = params.get("update")
    if not isinstance(upd, dict):
        return
    kind = upd.get("sessionUpdate")
    if not kind:
        return

    if kind == "agent_message_chunk":
        chunk = _extract_acp_chunk_text(upd)
        if chunk:
            state.accumulated_assistant_text.append(chunk)
            if on_event is not None:
                await on_event({"event": "text_delta", "text": chunk})
        return

    if kind == "agent_thought_chunk":
        chunk = _extract_acp_chunk_text(upd)
        if chunk and on_event is not None:
            await on_event({"event": "thinking_delta", "text": chunk})
        return

    if kind == "tool_call":
        tid = str(upd.get("toolCallId") or upd.get("tool_call_id") or "")
        if not tid:
            return
        title = str(upd.get("title") or "").strip()

        if title.startswith("MCP:"):
            state.mcp_opaque_ids.add(tid)
            cb = hooks.on_opaque_tool_call if hooks is not None else None
            if cb is not None and on_event is not None and state.conversation_id:
                await cb(state.conversation_id, on_event)
            return

        tool_kind = upd.get("kind")
        name = title if title else (str(tool_kind) if tool_kind is not None else "tool")
        state.tool_display_names[tid] = name
        args: dict[str, Any] = {}
        raw_in = upd.get("rawInput")
        if raw_in is None:
            raw_in = upd.get("raw_input")
        if isinstance(raw_in, str) and raw_in.strip():
            try:
                parsed = json.loads(raw_in)
                args = parsed if isinstance(parsed, dict) else {"rawInput": raw_in}
            except json.JSONDecodeError:
                args = {"rawInput": raw_in}
        elif isinstance(raw_in, dict):
            args = raw_in
        else:
            st = upd.get("status")
            args = {"kind": tool_kind, "status": st} if st is not None or tool_kind is not None else {}
        state.tool_arguments[tid] = dict(args)
        if on_event is not None:
            await on_event(
                {
                    "event": "tool_call",
                    "id": tid,
                    "name": name,
                    "arguments": dict(args),
                }
            )
        return

    if kind == "tool_call_update":
        tid = str(upd.get("toolCallId") or upd.get("tool_call_id") or "")
        if not tid:
            return

        if tid in state.mcp_opaque_ids:
            return

        status = upd.get("status")
        status_l = str(status).lower() if status is not None else ""

        if status_l == "in_progress":
            return

        if status_l not in _TERMINAL_TOOL_STATUSES:
            return
        if tid in state.completed_tool_call_ids:
            return
        state.completed_tool_call_ids.add(tid)

        body = _tool_call_update_result_text(upd)
        result_s = body if body else status_l
        title_upd = str(upd.get("title") or "").strip()
        name = title_upd if title_upd else state.tool_display_names.get(tid, "tool")
        if title_upd:
            state.tool_display_names[tid] = title_upd
        raw_args = state.tool_arguments.get(tid, {})
        norm = normalize_acp_tool_round(tid, name, raw_args, result_s)
        state.completed_tool_rounds.append(norm)
        registry_name = str(norm["name"])
        if on_event is not None:
            await on_event(
                {
                    "event": "tool_result",
                    "id": tid,
                    "name": registry_name,
                    "result": result_s,
                }
            )
        return

    log.debug("cursor acp: unhandled sessionUpdate %s", kind)
