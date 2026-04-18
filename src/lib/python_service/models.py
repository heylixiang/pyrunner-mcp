from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lib.sandbox_runner import SandboxExecutionResult, SandboxSessionConfig


@dataclass
class PythonExecutionPolicy:
    additional_authorized_imports: list[str] = field(default_factory=list)
    max_print_outputs_length: int | None = None
    executor_timeout_seconds: float | None = None
    sandbox_response_timeout_seconds: float = 5.0
    workdir: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    functions_module: str | None = None

    def to_sandbox_config(self, *, initial_variables: dict[str, Any] | None = None) -> SandboxSessionConfig:
        return SandboxSessionConfig(
            additional_authorized_imports=list(self.additional_authorized_imports),
            max_print_outputs_length=self.max_print_outputs_length,
            timeout_seconds=self.executor_timeout_seconds,
            initial_variables=dict(initial_variables or {}),
            response_timeout_seconds=self.sandbox_response_timeout_seconds,
            workdir=self.workdir,
            env=dict(self.env),
            functions_module=self.functions_module,
        )


@dataclass
class PythonExecutionResult:
    session_id: str
    output: Any = None
    logs: str = ""
    is_final_answer: bool = False
    error_type: str | None = None
    error_message: str | None = None
    error_traceback: str | None = None
    timed_out: bool = False

    @classmethod
    def from_sandbox_result(cls, session_id: str, result: SandboxExecutionResult) -> "PythonExecutionResult":
        error_type = result.error.type if result.error else None
        error_message = result.error.message if result.error else None
        error_traceback = result.error.traceback if result.error else None
        return cls(
            session_id=session_id,
            output=result.output,
            logs=result.logs,
            is_final_answer=result.is_final_answer,
            error_type=error_type,
            error_message=error_message,
            error_traceback=error_traceback,
            timed_out=result.timed_out,
        )


@dataclass
class PythonSessionInfo:
    session_id: str
    policy: PythonExecutionPolicy
    workdir: str | None = None
