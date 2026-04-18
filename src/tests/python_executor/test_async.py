from __future__ import annotations

import asyncio
from textwrap import dedent

import pytest

from lib.python_executor import LocalPythonExecutor
from lib.python_executor.errors import ExecutionTimeoutError


class DemoAsyncIterator:
    def __init__(self, values: list[int]):
        self._values = iter(values)

    def __aiter__(self) -> "DemoAsyncIterator":
        return self

    async def __anext__(self) -> int:
        try:
            value = next(self._values)
        except StopIteration as exc:
            raise StopAsyncIteration from exc
        await asyncio.sleep(0)
        return value


class DemoAsyncContextManager:
    async def __aenter__(self) -> str:
        await asyncio.sleep(0)
        return "entered"

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        await asyncio.sleep(0)
        return False


async def fetch_user_info(user_id: str) -> dict[str, str]:
    await asyncio.sleep(0)
    return {
        "id": user_id,
        "name": f"user-{user_id}",
        "email": f"user-{user_id}@example.com",
    }


async def slow_value() -> str:
    await asyncio.sleep(0.2)
    return "done"


def stream_values() -> DemoAsyncIterator:
    return DemoAsyncIterator([1, 2, 3])


def make_async_context() -> DemoAsyncContextManager:
    return DemoAsyncContextManager()


def test_executor_supports_top_level_await_with_async_helper():
    executor = LocalPythonExecutor(additional_functions={"fetch_user_info": fetch_user_info})

    result = executor("await fetch_user_info('1')")

    assert result.output == {
        "id": "1",
        "name": "user-1",
        "email": "user-1@example.com",
    }


def test_executor_supports_async_defs_async_for_and_async_with():
    executor = LocalPythonExecutor(
        additional_functions={
            "stream_values": stream_values,
            "make_async_context": make_async_context,
        }
    )

    result = executor(
        dedent(
            """\
            async def main():
                def double(value):
                    return value * 2

                doubled = list(map(double, [1, 2, 3]))
                total = 0
                async for value in stream_values():
                    total += value
                async with make_async_context() as token:
                    marker = token
                return doubled, total, marker

            await main()
            """
        )
    )

    assert result.output == ([2, 4, 6], 6, "entered")


def test_async_execution_path_honors_timeout():
    executor = LocalPythonExecutor(
        additional_functions={"slow_value": slow_value},
        timeout_seconds=0.05,
    )

    with pytest.raises(ExecutionTimeoutError):
        executor("await slow_value()")
