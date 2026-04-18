from __future__ import annotations

from textwrap import dedent

import pytest

from lib.python_executor import LocalPythonExecutor, evaluate_python_code
from lib.python_executor.errors import InterpreterError


def assert_interpreter_error(code: str, executor: LocalPythonExecutor | None = None) -> InterpreterError:
    executor = executor or LocalPythonExecutor()
    with pytest.raises(InterpreterError) as exc_info:
        executor(code)
    return exc_info.value


def test_forbids_unauthorized_module_import():
    error = assert_interpreter_error("import os")
    assert "Import of os is not allowed" in str(error)


def test_forbids_relative_imports():
    error = assert_interpreter_error(
        dedent(
            """\
            from .utils import truncate_content
            """
        )
    )
    assert "Relative imports are not supported" in str(error)


def test_forbids_access_to_undefined_dangerous_builtins():
    for code in ("eval('1 + 1')", "open('tmp.txt', 'w')"):
        error = assert_interpreter_error(code)
        assert "Forbidden function evaluation" in str(error)


def test_forbids_dunder_attribute_access_by_dot_and_getattr():
    dot_error = assert_interpreter_error(
        dedent(
            """\
            value = "x"
            value.__class__
            """
        )
    )
    getattr_error = assert_interpreter_error("getattr('x', '__class__')")

    assert "Forbidden access to dunder attribute" in str(dot_error)
    assert "Forbidden access to dunder attribute" in str(getattr_error)


def test_forbids_overwriting_static_tool_names():
    error = assert_interpreter_error("len = 3")
    assert "Cannot assign to name 'len'" in str(error)


def test_forbids_leaking_dangerous_function_from_static_tool():
    executor = LocalPythonExecutor(additional_functions={"give_eval": lambda: eval})
    error = assert_interpreter_error("give_eval()", executor=executor)

    assert "Forbidden access to function: eval" in str(error)


def test_forbids_leaking_unauthorized_module_from_static_tool():
    executor = LocalPythonExecutor(additional_functions={"give_os": lambda: __import__("os")})
    error = assert_interpreter_error("give_os()", executor=executor)

    assert "Forbidden access to module: os" in str(error)


def test_forbids_access_to_dangerous_function_from_authorized_module():
    with pytest.raises(InterpreterError) as exc_info:
        evaluate_python_code(
            dedent(
                """\
                import builtins
                builtins.eval
                """
            ),
            authorized_imports=["builtins"],
        )

    assert "Forbidden access to function: eval" in str(exc_info.value)


def test_forbids_calling_builtin_not_in_static_tools_even_if_imported():
    with pytest.raises(InterpreterError) as exc_info:
        evaluate_python_code(
            dedent(
                """\
                import builtins
                builtins.open("tmp.txt", "w")
                """
            ),
            authorized_imports=["builtins"],
        )

    assert "Forbidden access to function: open" in str(exc_info.value)
