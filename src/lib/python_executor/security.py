from __future__ import annotations

import inspect
from collections.abc import Callable
from functools import wraps
from types import BuiltinFunctionType, FunctionType, ModuleType
from typing import Any

from .constants import DANGEROUS_FUNCTIONS
from .errors import InterpreterError
from .models import EvaluationContext
from .runtime import is_final_answer_tool


def build_import_tree(authorized_imports: list[str]) -> dict[str, Any]:
    tree: dict[str, Any] = {}
    for import_path in authorized_imports:
        parts = import_path.split(".")
        current = tree
        for part in parts:
            current = current.setdefault(part, {})
    return tree


def check_import_authorized(import_to_check: str, authorized_imports: list[str]) -> bool:
    current_node = build_import_tree(authorized_imports)
    for part in import_to_check.split("."):
        if "*" in current_node:
            return True
        if part not in current_node:
            return False
        current_node = current_node[part]
    return True


def check_safer_result(
    result: Any,
    static_tools: dict[str, Callable] | None = None,
    authorized_imports: list[str] | None = None,
) -> None:
    authorized_imports = authorized_imports or []

    if isinstance(result, ModuleType):
        if not check_import_authorized(result.__name__, authorized_imports):
            raise InterpreterError(f"Forbidden access to module: {result.__name__}")
        return

    if isinstance(result, dict) and result.get("__spec__"):
        module_name = result["__name__"]
        if not check_import_authorized(module_name, authorized_imports):
            raise InterpreterError(f"Forbidden access to module: {module_name}")
        return

    if not isinstance(result, (FunctionType, BuiltinFunctionType)):
        return

    for qualified_function_name in DANGEROUS_FUNCTIONS:
        module_name, function_name = qualified_function_name.rsplit(".", 1)
        if (
            (static_tools is None or function_name not in static_tools)
            and result.__name__ == function_name
            and result.__module__ == module_name
        ):
            raise InterpreterError(f"Forbidden access to function: {function_name}")


def safer_eval_async(func: Callable):
    @wraps(func)
    async def wrapper(expression, ctx: EvaluationContext):
        result = await func(expression, ctx)
        check_safer_result(result, ctx.static_tools, ctx.authorized_imports)
        return result

    return wrapper


def safer_func(func: Callable, ctx: EvaluationContext):
    if isinstance(func, type) or is_final_answer_tool(func):
        return func

    @wraps(func)
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        if inspect.isawaitable(result):
            async def await_and_check():
                awaited_result = await result
                check_safer_result(awaited_result, ctx.static_tools, ctx.authorized_imports)
                return awaited_result

            return await_and_check()
        check_safer_result(result, ctx.static_tools, ctx.authorized_imports)
        return result

    return wrapper


def get_safe_module(raw_module, authorized_imports, visited=None):
    """Return a shallow-safe module clone while preserving nested module references."""
    if not isinstance(raw_module, ModuleType):
        return raw_module

    if visited is None:
        visited = set()

    module_id = id(raw_module)
    if module_id in visited:
        return raw_module

    visited.add(module_id)
    safe_module = ModuleType(raw_module.__name__)

    for attr_name in dir(raw_module):
        try:
            attr_value = getattr(raw_module, attr_name)
        except (ImportError, AttributeError):
            continue
        if isinstance(attr_value, ModuleType):
            attr_value = get_safe_module(attr_value, authorized_imports, visited=visited)
        setattr(safe_module, attr_name, attr_value)

    return safe_module
