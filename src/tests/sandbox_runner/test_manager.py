from __future__ import annotations

from textwrap import dedent

from lib.sandbox_runner import SandboxRunnerManager, SandboxSessionConfig


def test_session_executes_code_and_persists_state():
    manager = SandboxRunnerManager()
    with manager.create_session() as session:
        first = session.execute(
            dedent(
                """\
                print("hello")
                value = 21
                value
                """
            )
        )
        second = session.execute("value * 2")

    assert first.error is None
    assert first.output == 21
    assert first.logs.strip() == "hello"
    assert second.error is None
    assert second.output == 42


def test_session_executes_async_code():
    manager = SandboxRunnerManager()
    with manager.create_session() as session:
        result = session.execute(
            dedent(
                """\
                async def compute():
                    return 6 * 7

                await compute()
                """
            )
        )

    assert result.error is None
    assert result.output == 42


def test_session_accepts_host_variables():
    manager = SandboxRunnerManager()
    with manager.create_session() as session:
        session.send_variables({"base": 10})
        result = session.execute("base + 5")

    assert result.error is None
    assert result.output == 15


def test_session_returns_structured_interpreter_errors():
    manager = SandboxRunnerManager()
    with manager.create_session() as session:
        result = session.execute("import os")

    assert result.error is not None
    assert result.error.type == "InterpreterError"
    assert "Import of os is not allowed" in result.error.message


def test_manager_kills_worker_on_response_timeout_and_recovers():
    manager = SandboxRunnerManager()
    with manager.create_session(SandboxSessionConfig(timeout_seconds=None, response_timeout_seconds=0.05)) as session:
        timed_out = session.execute(
            dedent(
                """\
                import time
                time.sleep(0.2)
                """
            )
        )
        recovered = session.execute("6 * 7")

    assert timed_out.error is not None
    assert timed_out.error.type == "SandboxTimeoutError"
    assert timed_out.timed_out is True
    assert recovered.error is None
    assert recovered.output == 42


def test_manager_restarts_after_executor_internal_timeout():
    manager = SandboxRunnerManager()
    with manager.create_session(SandboxSessionConfig(timeout_seconds=0.05, response_timeout_seconds=1.0)) as session:
        timed_out = session.execute(
            dedent(
                """\
                import time
                time.sleep(0.2)
                """
            )
        )
        recovered = session.execute("(1, {2, 3})")

    assert timed_out.error is not None
    assert timed_out.error.type == "ExecutionTimeoutError"
    assert recovered.error is None
    assert recovered.output == (1, {2, 3})
