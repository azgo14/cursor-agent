#!/usr/bin/env python3
"""Minimal runnable example for Cursor ACP.

From the repo root (after an editable install):

    pip install -e .
    python examples/quickstart.py

With a custom working directory and prompt:

    python examples/quickstart.py --cwd /path/to/repo "What does this project do?"

Requires ``agent`` on PATH and ``CURSOR_API_KEY`` or ``CURSOR_AUTH_TOKEN`` (unless
you pass ``--api-key`` / ``--auth-token``).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from cursor_agent import CursorAgent


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "prompt",
        nargs="?",
        default="Summarize this repository",
        help="Prompt to send to the agent (default: summarize repo)",
    )
    p.add_argument(
        "--cwd",
        type=Path,
        default=Path.cwd(),
        help="Working directory for the ACP session (default: current directory)",
    )
    p.add_argument(
        "--api-key",
        dest="api_key",
        default=None,
        help="Cursor API key (default: env CURSOR_API_KEY)",
    )
    p.add_argument(
        "--auth-token",
        dest="auth_token",
        default=None,
        help="Cursor auth token (default: env CURSOR_AUTH_TOKEN)",
    )
    return p.parse_args()


async def main() -> int:
    args = parse_args()
    cwd = args.cwd.resolve()
    if not cwd.is_dir():
        print(f"Not a directory: {cwd}", file=sys.stderr)
        return 2

    agent = CursorAgent(
        cwd=cwd,
        api_key=args.api_key,
        auth_token=args.auth_token,
    )
    try:
        result = await agent.prompt(args.prompt)
    except BaseException as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        await agent.shutdown()

    print(result.text)
    if result.completed_tool_rounds:
        print(f"\n(completed tool rounds: {len(result.completed_tool_rounds)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
