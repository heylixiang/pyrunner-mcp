from __future__ import annotations

import asyncio

from lib.python_executor import LocalPythonExecutor
from lib.sandbox_api import SandboxAPI


def test_executor_can_call_additional_functions():
    """Functions registered via additional_functions are callable in sandbox code."""

    def double(x: int) -> int:
        return x * 2

    def greet(name: str) -> str:
        return f"Hello, {name}!"

    executor = LocalPythonExecutor(
        additional_functions={"double": double, "greet": greet},
        timeout_seconds=5,
    )
    result = executor("double(21)")
    assert result.output == 42

    result2 = executor("greet('world')")
    assert result2.output == "Hello, world!"


def test_sandbox_api_stub_marks_async_functions():
    api = SandboxAPI()

    @api.function
    async def fetch_user_info(user_id: str) -> dict[str, str]:
        await asyncio.sleep(0)
        return {"id": user_id}

    stub = api.stub()

    assert "async def fetch_user_info(user_id: str) -> dict:" in stub
