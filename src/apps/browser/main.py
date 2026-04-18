from typing import Any
from apps.browser.apis import api as sandbox_api, close_browser
from .config import settings
from lib.mcp_server import MCPApp, ToolExecutionError
from lib.python_service import (
    PythonExecutionPolicy,
    PythonExecutionResult,
    PythonExecutionService,
    SessionNotFoundError,
)
from lib.sandbox_runner.protocol import serialize_value

_SANDBOX_API_MODULE = "apps.browser.apis"

default_policy = PythonExecutionPolicy(
    additional_authorized_imports=list(settings.authorized_imports),
    executor_timeout_seconds=settings.executor_timeout_seconds,
    sandbox_response_timeout_seconds=settings.sandbox_response_timeout_seconds,
    functions_module=_SANDBOX_API_MODULE,
)

service = PythonExecutionService()

mcp = MCPApp(
    "PyRunner MCP",
    instructions=(
        "Call execute_python_code to run Python code. "
        "A session is created automatically if sessionId is omitted; "
        "reuse the returned sessionId for subsequent calls to keep state. "
        "Read the api://functions resource first to see available helper functions."
    ),
)
mcp.add_cleanup(service.close)
mcp.add_cleanup(close_browser)


@mcp.resource("api://functions", description="Helper functions available inside the Python sandbox.")
def list_sandbox_functions() -> dict[str, Any]:
    """Return Python-style stubs for all sandbox-callable functions."""
    return {
        "uri": "api://functions",
        "mimeType": "text/x-python",
        "text": sandbox_api.stub(),
    }


@mcp.tool
def execute_python_code(
    code: str,
    sessionId: str | None = None,
    variables: dict[str, Any] | None = None,
    responseTimeoutSeconds: float | None = None,
) -> dict[str, Any]:
    """Execute Python code. If sessionId is omitted a new session is created automatically.
    In this Python sandbox, prefer top-level `await` for async helpers.
    """
    if sessionId is None:
        session = service.create_session(policy=default_policy)
        sessionId = session.session_id

    try:
        result = service.execute_code(
            sessionId,
            code,
            variables=variables,
            response_timeout_seconds=responseTimeoutSeconds,
        )
    except SessionNotFoundError as exc:
        raise _session_error(exc, session_id=sessionId) from exc

    ok = result.error_type is None and not result.timed_out
    return {
        "kind": "python_execution",
        "ok": ok,
        "sessionId": sessionId,
        "result": _execution_result_to_dict(result),
    }


def main() -> int:
    return mcp.run(settings.mcp)


def _execution_result_to_dict(result: PythonExecutionResult) -> dict[str, Any]:
    return {
        "sessionId": result.session_id,
        "output": serialize_value(result.output),
        "logs": result.logs,
        "isFinalAnswer": result.is_final_answer,
        "timedOut": result.timed_out,
        "error": (
            {
                "type": result.error_type,
                "message": result.error_message,
                "traceback": result.error_traceback,
            }
            if result.error_type is not None
            else None
        ),
    }


def _session_error(exc: SessionNotFoundError, *, session_id: str) -> ToolExecutionError:
    return ToolExecutionError(
        str(exc),
        structured_content={
            "kind": "python_session_error",
            "ok": False,
            "details": {"sessionId": session_id},
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
