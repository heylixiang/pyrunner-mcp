from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from tempfile import TemporaryDirectory
from threading import Lock, Thread
from typing import Any

from .protocol import (
    SandboxError,
    SandboxExecutionResult,
    SandboxRequest,
    SandboxResponse,
    deserialize_value,
    serialize_value,
)


class SandboxProcessError(RuntimeError):
    """Raised when the sandbox worker process cannot be started or communicated with."""


@dataclass
class SandboxSessionConfig:
    additional_authorized_imports: list[str] = field(default_factory=list)
    max_print_outputs_length: int | None = None
    timeout_seconds: float | None = None
    initial_variables: dict[str, Any] = field(default_factory=dict)
    response_timeout_seconds: float = 5.0
    workdir: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    functions_module: str | None = None


class SandboxSession:
    STARTUP_TIMEOUT_SECONDS = 1.0

    def __init__(self, manager: "SandboxRunnerManager", config: SandboxSessionConfig):
        self.manager = manager
        self.config = config
        self._request_lock = Lock()
        self._response_queue: Queue[SandboxResponse | Exception | None] | None = None
        self._stderr_lines: list[str] = []
        self._process: subprocess.Popen[str] | None = None
        self._stdout_thread: Thread | None = None
        self._stderr_thread: Thread | None = None
        self._tempdir: TemporaryDirectory[str] | None = None
        self.workdir: Path | None = None
        with self._request_lock:
            self._start_process_locked()

    def _next_request_id(self) -> str:
        return uuid.uuid4().hex

    def _build_workdir(self) -> Path:
        if self.config.workdir:
            workdir = Path(self.config.workdir)
            workdir.mkdir(parents=True, exist_ok=True)
            return workdir
        self._tempdir = TemporaryDirectory(prefix="sandbox-runner-", dir=self.manager.default_workdir_root)
        return Path(self._tempdir.name)

    def _build_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update({
            "PYTHONPATH": str(self.manager.src_root),
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
        })
        env.update(self.config.env)
        return env
        
    def _collect_stderr(self, stream, stderr_lines: list[str]) -> None:
        try:
            for line in stream:
                stderr_lines.append(line)
        finally:
            stream.close()

    def _collect_stdout(self, stream, response_queue: Queue[SandboxResponse | Exception | None]) -> None:
        try:
            for line in stream:
                try:
                    response = SandboxResponse.from_json(line)
                except Exception as exc:
                    response_queue.put(exc)
                    continue
                response_queue.put(response)
        finally:
            response_queue.put(None)
            stream.close()

    def _start_process_locked(self) -> None:
        self.workdir = self._build_workdir()
        self._stderr_lines = []
        self._response_queue = Queue()
        stderr_lines = self._stderr_lines
        response_queue = self._response_queue

        # Some dev/debug launchers may clobber environment variables like PYTHONPATH.
        # Ensure the worker can always import `lib.*` by injecting src_root into sys.path.
        run_worker_code = (
            "import runpy, sys\n"
            f"sys.path.insert(0, {str(self.manager.src_root)!r})\n"
            f"runpy.run_module({self.manager.worker_module!r}, run_name='__main__')\n"
        )
        self._process = subprocess.Popen(
            [self.manager.python_executable, "-c", run_worker_code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            cwd=self.workdir,
            env=self._build_env(),
        )

        assert self._process.stdout is not None
        assert self._process.stderr is not None

        self._stdout_thread = Thread(
            target=self._collect_stdout,
            args=(self._process.stdout, response_queue),
            daemon=True,
        )
        self._stderr_thread = Thread(
            target=self._collect_stderr,
            args=(self._process.stderr, stderr_lines),
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

        response = self._send_request_locked(
            "init",
            {
                "additional_authorized_imports": serialize_value(self.config.additional_authorized_imports),
                "max_print_outputs_length": self.config.max_print_outputs_length,
                "timeout_seconds": self.config.timeout_seconds,
                "initial_variables": serialize_value(self.config.initial_variables),
                "functions_module": self.config.functions_module,
            },
            timeout=max(self.config.response_timeout_seconds, self.STARTUP_TIMEOUT_SECONDS),
        )
        if not response.ok:
            self._terminate_process()
            message = response.error.message if response.error else "Unknown init failure"
            raise SandboxProcessError(f"Failed to initialize sandbox worker: {message}")

    def _ensure_process_locked(self) -> None:
        if self._process is None or self._process.poll() is not None:
            self._terminate_process()
            self._start_process_locked()

    def _send_request_locked(self, action: str, payload: dict[str, Any], timeout: float) -> SandboxResponse:
        assert self._process is not None
        assert self._process.stdin is not None
        assert self._response_queue is not None

        request = SandboxRequest(request_id=self._next_request_id(), action=action, payload=payload)
        self._process.stdin.write(request.to_json() + "\n")
        self._process.stdin.flush()

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Timed out waiting for sandbox response to action '{action}'")
            try:
                item = self._response_queue.get(timeout=min(remaining, 0.1))
            except Empty:
                if self._process.poll() is not None:
                    raise SandboxProcessError(self._build_process_error_message(action))
                continue

            if item is None:
                raise SandboxProcessError(self._build_process_error_message(action))
            if isinstance(item, Exception):
                raise SandboxProcessError(f"Failed to decode sandbox response: {item}")
            if item.request_id != request.request_id:
                continue
            return item

    def _send_request(self, action: str, payload: dict[str, Any], timeout: float) -> SandboxResponse:
        with self._request_lock:
            self._ensure_process_locked()
            return self._send_request_locked(action, payload, timeout)

    def _build_process_error_message(self, action: str) -> str:
        stderr = "".join(self._stderr_lines).strip()
        message = f"Sandbox worker exited unexpectedly while handling '{action}'"
        if stderr:
            message += f": {stderr}"
        return message

    def _timeout_result(self, message: str) -> SandboxExecutionResult:
        self._terminate_process()
        return SandboxExecutionResult(
            error=SandboxError(type="SandboxTimeoutError", message=message),
            timed_out=True,
        )

    def _error_result_from_response(self, response: SandboxResponse) -> SandboxExecutionResult:
        execution_payload = response.payload.get("execution_result", {})
        result = SandboxExecutionResult(
            output=deserialize_value(execution_payload.get("output")),
            logs=execution_payload.get("logs", ""),
            is_final_answer=execution_payload.get("is_final_answer", False),
            error=response.error,
            timed_out=execution_payload.get("timed_out", False),
        )
        if response.error and response.error.type == "ExecutionTimeoutError":
            self._terminate_process()
        return result

    def execute(
        self,
        code: str,
        *,
        variables: dict[str, Any] | None = None,
        response_timeout_seconds: float | None = None,
    ) -> SandboxExecutionResult:
        timeout = response_timeout_seconds or self.config.response_timeout_seconds
        try:
            response = self._send_request(
                "execute",
                {
                    "code": code,
                    "variables": serialize_value(variables or {}),
                },
                timeout=timeout,
            )
        except TimeoutError:
            return self._timeout_result(
                f"Sandbox worker exceeded the response timeout of {timeout} seconds and was terminated."
            )
        except SandboxProcessError as exc:
            self._terminate_process()
            return SandboxExecutionResult(error=SandboxError(type="SandboxProcessError", message=str(exc)))

        if not response.ok:
            return self._error_result_from_response(response)

        execution_payload = response.payload["execution_result"]
        return SandboxExecutionResult(
            output=deserialize_value(execution_payload.get("output")),
            logs=execution_payload.get("logs", ""),
            is_final_answer=execution_payload.get("is_final_answer", False),
            timed_out=execution_payload.get("timed_out", False),
        )

    def send_variables(self, variables: dict[str, Any]) -> None:
        response = self._send_request(
            "send_variables",
            {"variables": serialize_value(variables)},
            timeout=self.config.response_timeout_seconds,
        )
        if not response.ok:
            message = response.error.message if response.error else "Failed to send variables"
            raise SandboxProcessError(message)

    def ping(self) -> bool:
        response = self._send_request("ping", {}, timeout=self.config.response_timeout_seconds)
        return response.ok and response.payload.get("status") == "pong"

    def reset(self) -> None:
        with self._request_lock:
            self._terminate_process()
            self._start_process_locked()

    def _terminate_process(self) -> None:
        process = self._process
        self._process = None
        self._response_queue = None

        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
            finally:
                process.wait(timeout=5)

        if self._tempdir is not None:
            self._tempdir.cleanup()
            self._tempdir = None

    def close(self) -> None:
        if self._process is not None and self._process.poll() is None:
            try:
                self._send_request("shutdown", {}, timeout=min(self.config.response_timeout_seconds, 1.0))
            except Exception:
                pass
        self._terminate_process()

    def __enter__(self) -> "SandboxSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class SandboxRunnerManager:
    def __init__(
        self,
        *,
        python_executable: str | None = None,
        worker_module: str = "lib.sandbox_runner.worker",
        src_root: str | Path | None = None,
        default_workdir_root: str | os.PathLike[str] | None = None,
    ):
        self.python_executable = python_executable or sys.executable
        self.worker_module = worker_module
        self.src_root = Path(src_root) if src_root is not None else Path(__file__).resolve().parents[2]
        self.default_workdir_root = default_workdir_root

    def create_session(self, config: SandboxSessionConfig | None = None) -> SandboxSession:
        return SandboxSession(self, config or SandboxSessionConfig())
