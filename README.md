# PyRunner MCP

[中文 README](./README.zh-CN.md)

PyRunner MCP is a self-hosted Python execution stack for AI agents. It combines:

- a restricted Python AST interpreter
- a worker-process runner with timeout kill and recovery
- a high-level session service
- a lightweight MCP server framework with stdio and Streamable HTTP transports

The project is designed for teams that want to run Python for agents without depending on an external sandbox product or a third-party MCP framework.

## Why This Project Exists

Most agent-oriented Python execution servers are thin wrappers around `exec`, a container runtime, or a prebuilt MCP SDK. This project takes a different approach:

- It implements its own restricted Python interpreter instead of calling raw `exec`.
- It runs execution in a dedicated worker process instead of the MCP process.
- It exposes a clean service layer for embedding, rather than forcing direct coupling to transport code.
- It implements its own MCP server framework, including JSON-RPC handling, tool registration, stdio transport, and Streamable HTTP transport.
- It supports injecting curated host-side helper functions into the Python sandbox and exposing their stubs as MCP resources.

This makes the system easier to study, extend, and harden incrementally.

## Key Features

- Restricted Python execution using an AST interpreter
- Session-based execution with state preserved across calls
- Top-level `await` support inside executed Python code
- Structured execution results with output, logs, timeout state, and error metadata
- Host-side worker timeout enforcement with automatic recovery
- Per-session temporary working directories when no custom workdir is provided
- Injection of curated Python-callable helper functions into the sandbox
- Helper API discovery through MCP resources such as `api://functions`
- Self-implemented MCP framework with decorator-based registration
- Multiple transports: `stdio` and `streamable-http`
- Optional auto-reload support for local development

## What It Does

PyRunner MCP lets an MCP client send Python code to a tool such as `execute_python_code`, run that code inside a restricted interpreter, keep state across calls with sessions, and receive structured results back.

Out of the box, the repository currently includes:

- `lib.python_executor`: restricted AST-based Python execution
- `lib.sandbox_runner`: worker-process isolation, request/response transport, timeout kill, restart, and per-session working directories
- `lib.python_service`: a clean programmatic session API for Python execution
- `lib.mcp_server`: a generic MCP framework with tools, resources, prompts, stdio transport, and Streamable HTTP transport
- `lib.sandbox_api`: a small registry for exposing host helper functions inside the sandbox and publishing Python-style stubs to agents
- `src/apps/features`: an MCP app example that injects database helper functions
- `src/apps/browser`: an MCP app example that injects Playwright-backed browser helper functions

## Architecture

The stack is intentionally layered:

1. `lib.python_executor`
   Restricted interpreter for Python code. Handles AST evaluation, allowlisted imports, controlled builtins, print capture, execution state, and execution limits.

2. `lib.sandbox_runner`
   Process boundary around the executor. Starts a worker process, sends requests over stdio, enforces host-side response timeouts, kills stuck workers, and recreates them when needed.

3. `lib.python_service`
   High-level API for creating sessions, executing code, resetting state, listing sessions, and closing sessions.

4. `lib.mcp_server`
   Generic MCP framework. Handles initialize flow, protocol validation, tool/resource/prompt registration, stdio transport, Streamable HTTP transport, and runtime options.

5. `src/apps/*`
   App-specific MCP entrypoints. These compose the generic layers into runnable MCP servers with domain-specific helper APIs.

## Security Model

This project improves control and isolation, but it is not yet a real OS-level sandbox.

### Implemented today

- Restricted AST evaluation instead of raw `exec`
- Allowlisted imports
- Dangerous function checks
- Dunder attribute restrictions
- Print capture
- Execution limits in the interpreter
- Dedicated worker-process execution
- Worker restart after crashes
- Host-side response timeout kill and recovery
- Isolated per-session working directories

### Not implemented yet

- Read-only filesystem mounts
- True filesystem sandboxing
- Network isolation
- CPU limits
- Memory limits
- PID / process count limits
- seccomp / AppArmor / gVisor / nsjail / cgroup isolation

### Practical takeaway

Treat this project as:

- safer than directly exposing `exec`
- more controllable than running everything in-process
- not equivalent to a hardened container sandbox

If you need strong isolation guarantees against hostile code, you still need OS-level sandboxing on top of this stack.

## Repository Layout

```text
src/
  apps/
    browser/         # MCP app with Playwright-backed helper functions
    features/        # MCP app with database-backed helper functions
  core/              # shared runtime configuration
  lib/
    mcp_server/      # generic MCP framework
    python_executor/ # restricted AST interpreter
    python_service/  # high-level session API
    sandbox_api/     # helper function registry + stub generation
    sandbox_runner/  # worker process management
  tests/
    functions/
    mcp_server/
    python_executor/
    python_service/
    sandbox_runner/
```

## Installation

This project uses `uv`.

### Base install

```bash
uv sync
```

### Install development dependencies

```bash
uv sync --group dev
```

### Install the database example app dependencies

```bash
uv sync --group dev --group features
```

### Install the browser example app dependencies

```bash
uv sync --group dev --group browser
```

## Running Tests

For the full test suite, install the development and `features` groups first:

```bash
uv sync --group dev --group features
```

From the repository root:

```bash
uv run pytest -q src/tests
```

You can also run targeted suites:

```bash
uv run pytest -q src/tests/python_executor src/tests/sandbox_runner src/tests/python_service src/tests/mcp_server
```

## Running an MCP Server

There is currently no single baked-in `src/main.py` entrypoint in this repository. Instead, the repo ships app-specific entrypoints under `src/apps/`.

### Example: run the database-oriented app over Streamable HTTP

```bash
uv run python src/apps/features/main.py
```

Default runtime settings:

- transport: `streamable-http`
- host: `127.0.0.1`
- port: `8094`
- path: `/mcp`
- health check: `/healthz`

### Run over stdio

```bash
PYRUNNER_MCP_TRANSPORT=stdio uv run python src/apps/features/main.py
```

### Example: run the browser-oriented app

```bash
uv run python src/apps/browser/main.py
```

This app requires the `browser` dependency group and a reachable Chromium instance for CDP connection.

## Runtime Configuration

Shared MCP runtime configuration is loaded from environment variables:

- `PYRUNNER_MCP_TRANSPORT`
- `PYRUNNER_MCP_HOST`
- `PYRUNNER_MCP_PORT`
- `PYRUNNER_MCP_PATH`
- `PYRUNNER_MCP_ALLOWED_ORIGINS`
- `PYRUNNER_RELOAD`

Fallback aliases are also supported:

- `MCP_TRANSPORT`
- `MCP_HOST`
- `MCP_PORT`
- `MCP_PATH`
- `MCP_ALLOWED_ORIGINS`

Python execution configuration:

- `PYRUNNER_AUTHORIZED_IMPORTS`
- `PYRUNNER_EXECUTOR_TIMEOUT_SECONDS`
- `PYRUNNER_SANDBOX_RESPONSE_TIMEOUT_SECONDS`

### App-specific configuration

Browser app:

- `BROWSER_HOST`
- `BROWSER_PORT`
- `BROWSER_TIMEOUT`
- `BROWSER_WS_URL`
- `BROWSER_HOST_HEADER`

Features app:

- `DB_HOST`
- `DB_PORT`
- `DB_USER`
- `DB_PASSWORD`
- `DB_NAME`

## MCP Behavior

The custom MCP framework currently supports:

- `initialize`
- `ping`
- `tools/list`
- `tools/call`
- `resources/list`
- `resources/templates/list`
- `resources/read`
- `prompts/list`
- `prompts/get`
- `notifications/initialized`
- `notifications/cancelled`

The example apps expose:

- tool: `execute_python_code`
- resource: `api://functions`

The `api://functions` resource returns Python-style stubs for the host helper functions that were injected into the sandbox.

## Python Execution Model

`execute_python_code` behaves as follows:

- If `sessionId` is omitted, a new session is created automatically.
- Reusing `sessionId` preserves Python state across calls.
- `variables` can be injected from the host into the active session before execution.
- `responseTimeoutSeconds` can override the host-side worker response timeout for a call.
- Top-level `await` is supported.
- Output, logs, timeout state, and structured error details are returned in a machine-friendly result object.

Typical response shape:

```json
{
  "kind": "python_execution",
  "ok": true,
  "sessionId": "f1f5c0f6...",
  "result": {
    "sessionId": "f1f5c0f6...",
    "output": 42,
    "logs": "hello",
    "isFinalAnswer": false,
    "timedOut": false,
    "error": null
  }
}
```

## Example MCP Flow

### 1. Initialize

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {
      "name": "example-client",
      "version": "1.0"
    }
  }
}
```

### 2. Read available helper functions

Call `resources/read` for `api://functions` so the agent sees the helper stubs that exist inside the sandbox.

### 3. Execute Python

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "execute_python_code",
    "arguments": {
      "code": "x = 10\nx + 32"
    }
  }
}
```

### 4. Reuse the returned session

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "execute_python_code",
    "arguments": {
      "sessionId": "returned-session-id",
      "code": "x * 2"
    }
  }
}
```

## Programmatic Usage

### Use the Python execution service directly

```python
from lib.python_service import PythonExecutionPolicy, PythonExecutionService

service = PythonExecutionService()

session = service.create_session(
    policy=PythonExecutionPolicy(
        additional_authorized_imports=["statistics"],
        sandbox_response_timeout_seconds=2.0,
    ),
    initial_variables={"numbers": [1, 2, 3]},
)

result = service.execute_code(
    session.session_id,
    "import statistics\nstatistics.mean(numbers)",
)

print(result.output)  # 2
service.close()
```

### Compose an MCP app

```python
from lib.mcp_server import MCPApp

mcp = MCPApp("Demo")

@mcp.tool
def ping() -> dict:
    return {"ok": True}

if __name__ == "__main__":
    raise SystemExit(mcp.run())
```

## Injecting Host Helper Functions

The repository includes `lib.sandbox_api.SandboxAPI`, which lets you:

- register Python callables that are safe and useful for the agent
- inject those callables into the interpreter
- generate Python-style stub text that can be published as an MCP resource

This is how the example apps expose browser helpers and database helpers without giving the sandbox unrestricted host access.
