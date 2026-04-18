# AGENTS

## Project Purpose

This project is building a safe and controllable Python execution stack for AI agents.

Current architecture:

1. `lib.python_executor`
   Restricted Python AST interpreter.
   Responsible for language-level controls such as allowed imports, allowed builtins, AST node evaluation, print capture, and execution state.

2. `lib.sandbox_runner`
   Internal process-isolation layer.
   Responsible for running `python_executor` in a separate worker process, request/response transport, worker restart, response timeout kill, and isolated working directories.

3. `lib.python_service`
   High-level service layer intended for future MCP or HTTP adapters.
   This is the preferred integration surface.
   MCP should call this layer instead of directly calling `sandbox_runner`.

4. `lib.mcp_server`
   Lightweight generic MCP framework layer.
   Responsible for JSON-RPC request handling, MCP initialize/tool routing, async tool execution, stdio transport, Streamable HTTP transport, tool schema inference, and decorator-based tool registration via `MCPApp`.

5. `src/main.py`
   Application entrypoint.
   This is where the project-specific MCP server is instantiated, Python execution tools are registered, and the server is started.

## Current Recommended Entry Point

Use:

- `lib.python_service.PythonExecutionService`
- `lib.mcp_server.MCPApp`
- `python src/main.py`

Do not treat these as the main external API:

- `lib.sandbox_runner.protocol`
- `lib.sandbox_runner.worker`
- `lib.sandbox_runner.manager`

Those are internal execution details.

## Source Layout

Important layout conventions:

- `src` is only the source root, not a Python package.
- There is no `src/__init__.py`.
- Imports should use `from lib.xxx import ...`, not `from src.lib.xxx import ...`.
- `python_executor` utilities live inside `src/lib/python_executor/`, not in top-level `src/utils.py`.

Current relevant directories:

- `src/lib/python_executor/`
- `src/lib/sandbox_runner/`
- `src/lib/python_service/`
- `src/lib/mcp_server/`
- `src/main.py`
- `src/tests/python_executor/`
- `src/tests/sandbox_runner/`
- `src/tests/python_service/`
- `src/tests/mcp_server/`

## Testing Conventions

Tests import from `lib...`.

Useful commands:

- From repo root:
  - `uv run pytest -q src/tests`
  - `uv run pytest -q src/tests/python_service src/tests/python_executor src/tests/sandbox_runner src/tests/mcp_server`
- From `src/`:
  - `uv run pytest -q tests`
- Run the MCP server:
  - `python src/main.py`
  - `uv run python src/main.py`

`pyproject.toml` contains:

- `[tool.pytest.ini_options]`
- `pythonpath = ["src"]`

So a custom `conftest.py` path hack is not needed.

## Current System-Layer Status

This section is important because the project originally documented a desired "system layer".

### Implemented now

- Separate worker process execution via `lib.sandbox_runner`
- Worker process lifecycle management
- Worker restart after crashes
- Host-side response timeout with worker kill
- Process cleanup and recovery
- Isolated working directory per sandbox session when `workdir` is not provided
- Optional custom workdir support
- Minimal controlled worker environment:
  - `PYTHONPATH`
  - `PYTHONUNBUFFERED`
  - `PYTHONIOENCODING`

### Partially implemented

- Timeout handling
  - `python_executor` still has an internal thread-based timeout
  - `sandbox_runner` adds a stronger host-side response timeout and kills the worker process on timeout
  - This is better than before, but still not a full OS-level resource-control solution

- Working directory isolation
  - Sessions run in separate directories
  - But filesystem access is not truly sandboxed

### Not implemented yet

- Read-only root filesystem
- Network isolation / disable outbound network
- CPU limits
- Memory limits
- PID / process count limits
- seccomp
- AppArmor
- gVisor
- nsjail
- cgroup-based isolation
- mount namespace / chroot style filesystem restrictions
- environment scrubbing beyond basic execution env setup

### Current assessment

The project now has:

- language-level restrictions
- process-level separation
- timeout kill/recovery

But it does **not** yet have true operating-system-level sandboxing.

## Current MCP Status

The project now includes:

- a generic MCP framework in `lib.mcp_server`
- a project-specific MCP app in `src/main.py`

Current transport:

- default Streamable HTTP transport
- stdio is still supported
- stdio uses newline-delimited JSON-RPC messages
- HTTP transport is implemented with `asyncio`

Currently implemented MCP request handling:

- `initialize`
- `ping`
- `tools/list`
- `tools/call`
- `notifications/initialized`
- `notifications/cancelled`

Currently exposed MCP tools:

- `create_python_session`
- `execute_python_code`
- `set_python_variables`
- `reset_python_session`
- `close_python_session`
- `list_python_sessions`

Implementation notes:

- `lib.mcp_server` is generic and does not hardcode Python-executor business tools
- `src/main.py` owns the concrete tool registration and startup wiring
- `lib.python_service` remains the clean programmatic integration layer
- `MCPApp` provides the user-facing API for app assembly:
  - constructor config
  - `@mcp.tool`
  - `mcp.run()`
- tool results return both:
  - `structuredContent`
  - a JSON text block in `content`
- session-oriented behavior is exposed directly to the agent through MCP tools

Runtime behavior of `src/main.py`:

- default transport is `streamable-http`
- default bind is `127.0.0.1:8000`
- default MCP endpoint path is `/mcp`
- health endpoint is `/healthz`
- stdio mode is available through env
- Streamable HTTP returns `Mcp-Session-Id` after successful initialization
- subsequent HTTP requests must include `Mcp-Session-Id`
- GET on the MCP endpoint opens an SSE stream when the session header is present

Supported environment variables:

- `PYRUNNER_MCP_TRANSPORT`
- `PYRUNNER_MCP_HOST`
- `PYRUNNER_MCP_PORT`
- `PYRUNNER_MCP_PATH`
- `PYRUNNER_MCP_ALLOWED_ORIGINS`
- fallback aliases:
  - `MCP_TRANSPORT`
  - `MCP_HOST`
  - `MCP_PORT`
  - `MCP_PATH`
  - `MCP_ALLOWED_ORIGINS`

## Current Behavior of `reset`

In `PythonExecutionService`, resetting a session means:

- restart the underlying sandbox worker
- preserve the session policy
- restore the session to its initial variables

It does **not** mean "empty everything".

## Security Notes

Current `python_executor` protections include:

- restricted AST evaluation instead of raw `exec`
- restricted imports
- dangerous function checks
- dunder attribute restrictions
- print capture instead of direct stdout usage
- max operations / while-iteration limits

Current known boundary:

- `python_executor` alone is not a real container
- real sandbox guarantees still require OS-level isolation work

## Current Tests

The project currently has tests covering:

- `python_executor` core behavior
- `python_executor` security boundaries
- `python_executor` execution limits
- `sandbox_runner` session behavior, timeout kill, restart, and recovery
- `python_service` session API and lifecycle behavior
- `mcp_server` generic framework registration and tool execution
- `main.py` initialize flow, tools discovery, stdio execution, Streamable HTTP execution, session lifecycle, and protocol/tool error handling

Current passing status at the time this file was written:

- `35 passed`

## Recommended Next Steps

Preferred next implementation direction:

1. Keep `sandbox_runner` internal
2. Consider adding an HTTP adapter only if needed, but continue to keep `python_service` as the main business layer
3. Add real OS-level sandbox controls:
   - disable network
   - filesystem isolation
   - CPU / memory / PID limits
   - seccomp / nsjail / gVisor style enforcement

## Files Worth Reading First

- `src/lib/python_service/service.py`
- `src/lib/python_service/models.py`
- `src/lib/mcp_server/app.py`
- `src/lib/mcp_server/server.py`
- `src/lib/mcp_server/tools.py`
- `src/lib/mcp_server/stdio.py`
- `src/lib/mcp_server/streamable_http.py`
- `src/main.py`
- `src/lib/sandbox_runner/manager.py`
- `src/lib/python_executor/executor.py`
- `src/lib/python_executor/evaluator.py`
- `src/tests/python_service/test_service.py`
- `src/tests/mcp_server/test_mcp_app.py`
- `src/tests/mcp_server/test_main_http.py`
- `src/tests/mcp_server/test_main_stdio.py`
- `src/tests/sandbox_runner/test_manager.py`
- `src/tests/python_executor/test_security.py`
