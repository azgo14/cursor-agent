"""Lightweight types for the cursor-agent public API."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional


StreamEventCallback = Callable[[dict[str, Any]], Any]  # may be async


class SessionEventKind(str, Enum):
    TEXT_DELTA = "text_delta"
    THINKING_DELTA = "thinking_delta"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ERROR = "error"


@dataclass(frozen=True)
class SessionEvent:
    """Normalized streaming event from ACP ``session/update``."""

    kind: SessionEventKind
    text: Optional[str] = None
    tool_id: Optional[str] = None
    name: Optional[str] = None
    arguments: Optional[dict[str, Any]] = None
    result: Optional[str] = None
    raw: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class ToolRound:
    """One completed tool call + result (matches Dream ``completed_tool_rounds`` shape)."""

    id: str
    name: str
    arguments: dict[str, Any]
    result: str


@dataclass
class PromptResult:
    """Outcome of a single ``session/prompt`` turn."""

    text: str
    completed_tool_rounds: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Message:
    """Optional minimal chat shape for future use (transcript building stays app-specific)."""

    role: str
    content: Optional[str] = None


@dataclass
class CursorAgentHooks:
    """Extension points for :class:`~cursor_agent.CursorAgent` lifecycle and events.

    All fields are optional; ``None`` means "no-op".  Callers supply only the
    hooks they need, and the package never imports application-specific code.

    * ``before_process_start`` — called with the working directory *before* the
      ACP subprocess is spawned (e.g. to write config files).
    * ``wrap_command`` — transforms the ``argv`` list + working-directory string
      into a single shell command string (e.g. for sandbox wrapping).  When
      ``None`` the argv is joined with :func:`shlex.join`.
    * ``on_opaque_tool_call`` — invoked when the ACP stream reports a tool call
      the package cannot handle natively (title starts with ``"MCP:"``).
      Receives ``(conversation_id, on_event)`` so the caller can bridge the
      call and emit real ``tool_call``/``tool_result`` events.
    """

    before_process_start: Optional[Callable[[Path], Awaitable[None]]] = None
    wrap_command: Optional[Callable[[list[str], str], Awaitable[str]]] = None
    on_opaque_tool_call: Optional[
        Callable[[str, Callable[[dict[str, Any]], Awaitable[None]]], Awaitable[None]]
    ] = None


@dataclass
class AgentConfig:
    """Configuration for :class:`cursor_agent.CursorAgent`."""

    cwd: Path
    api_key: Optional[str] = None
    auth_token: Optional[str] = None
    client_name: str = "cursor-agent"
    client_version: str = "0.1.1"
    prefer_composer_fast: bool = True
