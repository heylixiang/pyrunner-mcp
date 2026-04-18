from __future__ import annotations

from textwrap import dedent

import pytest

from lib.python_service import PythonExecutionPolicy, PythonExecutionService, SessionNotFoundError


def test_service_creates_session_and_executes_code():
    with PythonExecutionService() as service:
        session = service.create_session()
        first = service.execute_code(
            session.session_id,
            dedent(
                """\
                print("hello")
                value = 20
                value + 1
                """
            ),
        )
        second = service.execute_code(session.session_id, "value * 2")

    assert first.error_type is None
    assert first.output == 21
    assert first.logs.strip() == "hello"
    assert second.output == 40


def test_service_supports_policy_and_reset():
    policy = PythonExecutionPolicy(
        additional_authorized_imports=["time"],
        sandbox_response_timeout_seconds=1.0,
    )
    with PythonExecutionService() as service:
        session = service.create_session(policy=policy, initial_variables={"base": 5})
        result = service.execute_code(
            session.session_id,
            dedent(
                """\
                import time
                base = base + 2
                base
                """
            ),
        )
        service.reset_session(session.session_id)
        after_reset = service.execute_code(session.session_id, "base")

    assert result.error_type is None
    assert result.output == 7
    assert after_reset.error_type is None
    assert after_reset.output == 5


def test_service_exposes_structured_errors():
    with PythonExecutionService() as service:
        session = service.create_session()
        result = service.execute_code(session.session_id, "import os")

    assert result.error_type == "InterpreterError"
    assert "Import of os is not allowed" in result.error_message
    assert result.error_traceback is not None


def test_service_can_close_and_list_sessions():
    with PythonExecutionService() as service:
        first = service.create_session()
        second = service.create_session()
        session_ids = {session.session_id for session in service.list_sessions()}

        assert {first.session_id, second.session_id} == session_ids

        service.close_session(first.session_id)
        remaining = {session.session_id for session in service.list_sessions()}

    assert remaining == {second.session_id}


def test_service_raises_on_unknown_session():
    with PythonExecutionService() as service:
        with pytest.raises(SessionNotFoundError):
            service.execute_code("missing-session", "1 + 1")
