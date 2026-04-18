from __future__ import annotations

from textwrap import dedent

from lib.python_executor import LocalPythonExecutor, evaluate_python_code


class DemoContextManager:
    def __enter__(self):
        return "entered"

    def __exit__(self, exc_type, exc, tb):
        return False


def test_executor_keeps_state_and_captures_print_logs():
    executor = LocalPythonExecutor()

    first = executor(
        dedent(
            """\
            print("hello")
            x = 1 + 2
            x
            """
        )
    )
    second = executor("x + 4")

    assert first.output == 3
    assert first.logs.strip() == "hello"
    assert second.output == 7


def test_executor_supports_functions_classes_and_super_calls():
    executor = LocalPythonExecutor()

    result = executor(
        dedent(
            """\
            def add(a, b=1, *rest, **kwargs):
                total = a + b + sum(rest)
                if "bonus" in kwargs:
                    total += kwargs["bonus"]
                return total

            class A:
                def greet(self):
                    return "a"

            class B(A):
                def greet(self):
                    return super().greet() + "b"

            (add(1, 2, 3, bonus=4), B().greet())
            """
        )
    )

    assert result.output == (10, "ab")


def test_executor_supports_comprehensions_try_assert_delete_and_with():
    executor = LocalPythonExecutor(additional_functions={"DemoContextManager": DemoContextManager})

    result = executor(
        dedent(
            """\
            numbers = [1, 2, 3, 4]
            pairs = {n: n * n for n in numbers if n % 2 == 0}
            items = [n * 2 for n in numbers if n > 1]
            values = {n for n in numbers if n < 4}
            gen_total = sum(n for n in numbers if n > 2)
            try:
                assert len(items) == 3
                del numbers[0]
            except AssertionError:
                numbers = ["bad"]
            with DemoContextManager() as token:
                marker = token
            (pairs, items, values, gen_total, numbers, marker)
            """
        )
    )

    assert result.output == ({2: 4, 4: 16}, [4, 6, 8], {1, 2, 3}, 7, [2, 3, 4], "entered")


def test_executor_supports_import_and_final_answer_tool():
    executor = LocalPythonExecutor()
    executor.send_tools({"final_answer": lambda value: f"done:{value}"})

    result = executor(
        dedent(
            """\
            from math import *
            final_answer(str(int(sqrt(81))))
            """
        )
    )

    assert result.output == "done:9"
    assert result.is_final_answer is True


def test_evaluate_python_code_allows_explicit_additional_imports():
    output, is_final_answer = evaluate_python_code(
        dedent(
            """\
            import statistics
            statistics.mean([1, 2, 3])
            """
        ),
        authorized_imports=["statistics"],
    )

    assert output == 2
    assert is_final_answer is False
