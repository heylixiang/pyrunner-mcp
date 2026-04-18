from __future__ import annotations

import uuid
from dataclasses import dataclass
from threading import RLock
from typing import Any

from lib.sandbox_runner import SandboxRunnerManager, SandboxSession

from .models import PythonExecutionPolicy, PythonExecutionResult, PythonSessionInfo


class SessionNotFoundError(KeyError):
    """Raised when a requested Python execution session does not exist."""


@dataclass
class _ServiceSession:
    session_id: str
    policy: PythonExecutionPolicy
    sandbox_session: SandboxSession


class PythonExecutionService:
    """
    High-level session service intended to be wrapped by MCP or HTTP handlers.
    """

    def __init__(self, runner_manager: SandboxRunnerManager | None = None):
        self.runner_manager = runner_manager or SandboxRunnerManager()
        self._sessions: dict[str, _ServiceSession] = {}
        self._lock = RLock()

    def _generate_session_id(self) -> str:
        return uuid.uuid4().hex

    def _get_session(self, session_id: str) -> _ServiceSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise SessionNotFoundError(f"Unknown session_id: {session_id}") from exc

    def _session_info(self, entry: _ServiceSession) -> PythonSessionInfo:
        workdir = str(entry.sandbox_session.workdir) if entry.sandbox_session.workdir is not None else None
        return PythonSessionInfo(session_id=entry.session_id, policy=entry.policy, workdir=workdir)

    def create_session(
        self,
        *,
        policy: PythonExecutionPolicy | None = None,
        initial_variables: dict[str, Any] | None = None,
    ) -> PythonSessionInfo:
        policy = policy or PythonExecutionPolicy()
        session_id = self._generate_session_id()
        sandbox_session = self.runner_manager.create_session(
            policy.to_sandbox_config(initial_variables=initial_variables)
        )
        entry = _ServiceSession(session_id=session_id, policy=policy, sandbox_session=sandbox_session)
        with self._lock:
            self._sessions[session_id] = entry
        return self._session_info(entry)

    def execute_code(
        self,
        session_id: str,
        code: str,
        *,
        variables: dict[str, Any] | None = None,
        response_timeout_seconds: float | None = None,
    ) -> PythonExecutionResult:
        with self._lock:
            entry = self._get_session(session_id)
        sandbox_result = entry.sandbox_session.execute(
            code,
            variables=variables,
            response_timeout_seconds=response_timeout_seconds,
        )
        return PythonExecutionResult.from_sandbox_result(session_id, sandbox_result)

    def send_variables(self, session_id: str, variables: dict[str, Any]) -> None:
        with self._lock:
            entry = self._get_session(session_id)
        entry.sandbox_session.send_variables(variables)

    def reset_session(self, session_id: str) -> PythonSessionInfo:
        with self._lock:
            entry = self._get_session(session_id)
        entry.sandbox_session.reset()
        return self._session_info(entry)

    def get_session(self, session_id: str) -> PythonSessionInfo:
        with self._lock:
            entry = self._get_session(session_id)
        return self._session_info(entry)

    def list_sessions(self) -> list[PythonSessionInfo]:
        with self._lock:
            return [self._session_info(entry) for entry in self._sessions.values()]

    def close_session(self, session_id: str) -> None:
        with self._lock:
            entry = self._sessions.pop(session_id, None)
        if entry is None:
            raise SessionNotFoundError(f"Unknown session_id: {session_id}")
        entry.sandbox_session.close()

    def close(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for entry in sessions:
            entry.sandbox_session.close()

    def __enter__(self) -> "PythonExecutionService":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
