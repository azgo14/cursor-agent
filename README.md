# cursor-agent

Standalone Python client for **Cursor CLI** in **ACP** mode (`agent acp`): JSON-RPC over stdio to drive Composer from your own code — similar in spirit to `anthropic` or `google-genai`, but talking to the local `agent` binary instead of a hosted HTTP API.

## Requirements

- Python 3.11+
- [Cursor CLI](https://cursor.com/docs/cli/acp) with `agent` on `PATH` (often `~/.local/bin`)
- `CURSOR_API_KEY` or `CURSOR_AUTH_TOKEN` in the environment (or pass explicitly)

## Install

From this monorepo (path dependency):

```bash
uv add "cursor-agent @ file:./packages/cursor-agent"
```

Or after publishing to PyPI:

```bash
pip install cursor-agent
```

## Quick start

```python
import asyncio
from pathlib import Path
from cursor_agent import CursorAgent

async def main():
    agent = CursorAgent(cwd=Path("."))
    result = await agent.prompt("Summarize this repository")
    print(result.text)
    print(result.completed_tool_rounds)
    await agent.shutdown()

asyncio.run(main())
```

## Hooks

All extension points live in a single `CursorAgentHooks` dataclass — pass only
the hooks you need:

```python
from cursor_agent import CursorAgent, CursorAgentHooks

hooks = CursorAgentHooks(
    before_process_start=my_setup,     # async (cwd: Path) -> None
    wrap_command=my_sandbox_wrapper,   # async (argv, cwd_str) -> str
    on_opaque_tool_call=my_mcp_bridge, # async (conversation_id, on_event) -> None
)
agent = CursorAgent(cwd=Path("."), hooks=hooks)
```

- **`before_process_start`** — called with the working directory before the ACP subprocess spawns (e.g. write config files).
- **`wrap_command`** — transforms `argv` + working-directory string into a shell command (e.g. sandbox wrapping). Defaults to `shlex.join(argv)`.
- **`on_opaque_tool_call`** — invoked when the ACP stream reports a tool call the package cannot handle natively (title starts with `"MCP:"`). Receives `(conversation_id, on_event)` so the caller can bridge the call and emit real events.

## License

MIT
