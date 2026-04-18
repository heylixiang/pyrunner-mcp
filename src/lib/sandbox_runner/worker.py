from __future__ import annotations

import importlib
import sys
from typing import Any

from lib.python_executor import LocalPythonExecutor

from .protocol import (
    SandboxError,
    SandboxRequest,
    SandboxResponse,
    deserialize_value,
    serialize_value,
)


class SandboxWorker:
    def __init__(self):
        self.executor: LocalPythonExecutor | None = None
        self.config: dict[str, Any] = {}

    def handle_request(self, request: SandboxRequest) -> SandboxResponse:
        try:
            if request.action == "init":
                return self._handle_init(request)
            if request.action == "ping":
                return SandboxResponse(request_id=request.request_id, ok=True, payload={"status": "pong"})
            if request.action == "send_variables":
                return self._handle_send_variables(request)
            if request.action == "execute":
                return self._handle_execute(request)
            if request.action == "shutdown":
                return SandboxResponse(request_id=request.request_id, ok=True, payload={"status": "bye"})
            return SandboxResponse(
                request_id=request.request_id,
                ok=False,
                error=SandboxError(type="UnknownActionError", message=f"Unsupported action: {request.action}"),
            )
        except Exception as exc:
            return SandboxResponse(
                request_id=request.request_id,
                ok=False,
                error=SandboxError.from_exception(exc),
            )

    def _require_executor(self) -> LocalPythonExecutor:
        if self.executor is None:
            raise RuntimeError("Sandbox worker is not initialized")
        return self.executor

    def _build_executor(self, payload: dict[str, Any]) -> LocalPythonExecutor:
        additional_functions = self._load_sandbox_functions(payload.get("functions_module"))
        return LocalPythonExecutor(
            additional_authorized_imports=deserialize_value(payload.get("additional_authorized_imports", [])),
            max_print_outputs_length=payload.get("max_print_outputs_length"),
            timeout_seconds=payload.get("timeout_seconds"),
            additional_functions=additional_functions,
        )

    def _load_sandbox_functions(self, module_name: str | None) -> dict[str, Any]:
        if not module_name:
            return {}
        mod = importlib.import_module(module_name)
        api = getattr(mod, "api", None)
        if api is None:
            return {}
        return api.callables

    def _handle_init(self, request: SandboxRequest) -> SandboxResponse:
        self.config = request.payload.copy()
        self.executor = self._build_executor(request.payload)
        initial_variables = deserialize_value(request.payload.get("initial_variables", {}))
        if initial_variables:
            self.executor.send_variables(initial_variables)
        return SandboxResponse(request_id=request.request_id, ok=True, payload={"status": "initialized"})

    def _handle_send_variables(self, request: SandboxRequest) -> SandboxResponse:
        executor = self._require_executor()
        variables = deserialize_value(request.payload.get("variables", {}))
        executor.send_variables(variables)
        return SandboxResponse(request_id=request.request_id, ok=True, payload={"status": "variables-updated"})

    def _handle_execute(self, request: SandboxRequest) -> SandboxResponse:
        executor = self._require_executor()
        variables = deserialize_value(request.payload.get("variables", {}))
        if variables:
            executor.send_variables(variables)

        code = request.payload["code"]
        try:
            result = executor(code)
            return SandboxResponse(
                request_id=request.request_id,
                ok=True,
                payload={
                    "execution_result": {
                        "output": serialize_value(result.output),
                        "logs": result.logs,
                        "is_final_answer": result.is_final_answer,
                        "timed_out": False,
                    }
                },
            )
        except Exception as exc:
            logs = ""
            if executor.state.get("_print_outputs") is not None:
                logs = str(executor.state["_print_outputs"])
            return SandboxResponse(
                request_id=request.request_id,
                ok=False,
                payload={"execution_result": {"logs": logs, "timed_out": False}},
                error=SandboxError.from_exception(exc),
            )


def main() -> int:
    worker = SandboxWorker()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = SandboxRequest.from_json(line)
        response = worker.handle_request(request)
        print(response.to_json(), flush=True)
        if request.action == "shutdown":
            return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
