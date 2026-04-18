from __future__ import annotations

from textwrap import dedent

import pytest

from lib.python_executor import LocalPythonExecutor
from lib.python_executor import handlers_statements, models
from lib.python_executor.errors import ExecutionTimeoutError, InterpreterError


def test_truncates_print_output():
    executor = LocalPythonExecutor(max_print_outputs_length=40)

    result = executor("print('abcdefghijklmnopqrstuvwxyz' * 3)")

    assert "truncated" in result.logs
    assert len(result.logs) > 0


def test_timeout_raises_execution_timeout_error():
    executor = LocalPythonExecutor(additional_authorized_imports=["time"], timeout_seconds=0.05)

    with pytest.raises(ExecutionTimeoutError):
        executor(
            dedent(
                """\
                import time
                time.sleep(0.2)
                """
            )
        )


def test_while_loop_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(handlers_statements, "MAX_WHILE_ITERATIONS", 3)
    executor = LocalPythonExecutor()

    with pytest.raises(InterpreterError) as exc_info:
        executor(
            dedent(
                """\
                i = 0
                while True:
                    i += 1
                """
            )
        )

    assert "Maximum number of 3 iterations in While loop exceeded" in str(exc_info.value)


def test_operation_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(models, "MAX_OPERATIONS", 5)
    executor = LocalPythonExecutor()

    with pytest.raises(InterpreterError) as exc_info:
        executor(
            dedent(
                """\
                value = 0
                for i in range(10):
                    value += i
                """
            )
        )

    assert "Reached the max number of operations of 5" in str(exc_info.value)


def test_timeout_does_not_break_future_calls():
    executor = LocalPythonExecutor(additional_authorized_imports=["time"], timeout_seconds=0.05)

    with pytest.raises(ExecutionTimeoutError):
        executor(
            dedent(
                """\
                import time
                time.sleep(0.1)
                """
            )
        )

    executor.timeout_seconds = 1
    result = executor(
        dedent(
            """\
            x = 2
            x * 5
            """
        )
    )

    assert result.output == 10
