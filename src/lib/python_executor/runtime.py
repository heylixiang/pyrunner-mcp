from __future__ import annotations

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

from .errors import ExecutionTimeoutError


def run_coroutine_sync(awaitable, *, timeout_seconds: float | None = None):
    """Run an awaitable to completion from synchronous code."""

    async def runner():
        return await awaitable

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, runner())
        try:
            return future.result(timeout=timeout_seconds)
        except FuturesTimeoutError as exc:
            raise ExecutionTimeoutError(
                f"Code execution exceeded the maximum execution time of {timeout_seconds} seconds"
            ) from exc


class FinalAnswerTool:
    """Marker tool that turns a callable into a final_answer sentinel."""

    __python_executor_final_answer__ = True

    def __init__(self, handler):
        self._handler = handler

    def __call__(self, *args, **kwargs):
        return self._handler(*args, **kwargs)


def is_final_answer_tool(value: Any) -> bool:
    return getattr(value, "__python_executor_final_answer__", False) is True


def fix_final_answer_code(code: str) -> str:
    """
    Sometimes an LLM can try to assign a variable to final_answer, which would break the final_answer() tool.
    This function fixes this behaviour by replacing variable assignments to final_answer with final_answer_variable,
    while preserving function calls to final_answer().
    """
    assignment_pattern = r"(?<!\.)(?<!\w)\bfinal_answer\s*="
    if "final_answer(" not in code or not re.search(assignment_pattern, code):
        return code

    assignment_regex = r"(?<!\.)(?<!\w)(\bfinal_answer)(\s*=)"
    code = re.sub(assignment_regex, r"final_answer_variable\2", code)

    variable_regex = r"(?<!\.)(?<!\w)(\bfinal_answer\b)(?!\s*\()"
    return re.sub(variable_regex, "final_answer_variable", code)
