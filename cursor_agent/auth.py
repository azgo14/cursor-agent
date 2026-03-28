"""Build ``agent`` CLI argv with optional API key or auth token.

See https://cursor.com/docs/cli/acp
"""

from __future__ import annotations

import os
from typing import Optional


def cursor_cli_auth_configured(*, api_key: Optional[str] = None, token: Optional[str] = None) -> bool:
    """True if credentials are passed or set in the environment (non-empty)."""
    if api_key is not None and api_key.strip():
        return True
    if token is not None and token.strip():
        return True
    return bool((os.environ.get("CURSOR_API_KEY") or "").strip()) or bool(
        (os.environ.get("CURSOR_AUTH_TOKEN") or "").strip()
    )


def build_agent_argv(
    *parts: str,
    api_key: Optional[str] = None,
    auth_token: Optional[str] = None,
) -> list[str]:
    """Return argv starting with ``agent``, optional auth flags, then ``parts``.

    When ``api_key`` / ``auth_token`` are omitted, reads ``CURSOR_API_KEY`` /
    ``CURSOR_AUTH_TOKEN`` from the environment.
    """
    argv: list[str] = ["agent"]
    key = (api_key if api_key is not None else os.environ.get("CURSOR_API_KEY") or "").strip()
    tok = (auth_token if auth_token is not None else os.environ.get("CURSOR_AUTH_TOKEN") or "").strip()
    if key:
        argv.extend(["--api-key", key])
    elif tok:
        argv.extend(["--auth-token", tok])
    argv.extend(parts)
    return argv


def summarize_agent_argv_for_log(argv: list[str]) -> str:
    """Join argv for logs, redacting secret values after auth flags."""
    out: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] in ("--api-key", "--auth-token") and i + 1 < len(argv):
            out.append(argv[i])
            out.append("<redacted>")
            i += 2
        else:
            out.append(argv[i])
            i += 1
    return " ".join(out)
