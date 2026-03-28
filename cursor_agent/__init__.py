"""Python client for Cursor CLI Agent Client Protocol (ACP)."""

from cursor_agent.auth import (
    build_agent_argv,
    cursor_cli_auth_configured,
    summarize_agent_argv_for_log,
)
from cursor_agent.client import AcpSessionHandle, CursorAgent
from cursor_agent.session import AcpStreamState, dispatch_acp_session_update, normalize_acp_tool_round
from cursor_agent.transport import AcpTransport
from cursor_agent.types import (
    AgentConfig,
    CursorAgentHooks,
    Message,
    PromptResult,
    SessionEvent,
    ToolRound,
)

__all__ = [
    "AgentConfig",
    "AcpSessionHandle",
    "AcpStreamState",
    "AcpTransport",
    "CursorAgent",
    "CursorAgentHooks",
    "Message",
    "PromptResult",
    "SessionEvent",
    "ToolRound",
    "build_agent_argv",
    "cursor_cli_auth_configured",
    "dispatch_acp_session_update",
    "normalize_acp_tool_round",
    "summarize_agent_argv_for_log",
]
