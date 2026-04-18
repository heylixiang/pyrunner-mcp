from __future__ import annotations

import inspect
import textwrap
from collections.abc import Callable
from typing import Any, get_type_hints


class SandboxAPI:
    """
    Collects functions to be injected into the Python sandbox.

    Usage::

        api = SandboxAPI()

        @api.function
        def fetch_user_lists() -> list[dict]:
            \"\"\"获取所有用户列表。\"\"\"
            ...

        # pass api.callables to the executor as additional_functions
        # pass api.stub() to the agent as a prompt/resource
    """

    def __init__(self) -> None:
        self._functions: dict[str, Callable[..., Any]] = {}

    def function(
        self,
        func: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
    ):
        """Register a callable. Can be used as bare decorator or with arguments."""
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            fn_name = name or fn.__name__
            self._functions[fn_name] = fn
            return fn

        if func is not None:
            return decorator(func)
        return decorator

    @property
    def callables(self) -> dict[str, Callable[..., Any]]:
        """All registered functions, keyed by name. Pass to executor."""
        return dict(self._functions)

    def stub(self) -> str:
        """
        Generate Python-style stub text for all registered functions.

        Output looks like real function signatures with docstrings,
        suitable for embedding in an LLM prompt.
        """
        parts: list[str] = []
        for fn_name, fn in self._functions.items():
            parts.append(_build_stub(fn, fn_name))
        return "\n\n".join(parts)


def _build_stub(fn: Callable[..., Any], name: str) -> str:
    """Build a Python stub string for a single function."""
    sig = _build_signature(fn, name)
    doc = inspect.getdoc(fn)
    if doc:
        indented = textwrap.indent(doc, "    ")
        return f'{sig}\n    """\n{indented}\n    """'
    return f"{sig}\n    ..."


def _build_signature(fn: Callable[..., Any], name: str) -> str:
    """Build `def name(params) -> return_type:` line."""
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}

    params: list[str] = []
    for pname, param in sig.parameters.items():
        part = pname
        annotation = hints.get(pname)
        if annotation is not None:
            part += f": {_format_annotation(annotation)}"
        if param.default is not inspect.Parameter.empty:
            part += f" = {repr(param.default)}"
        params.append(part)

    ret = hints.get("return")
    ret_str = f" -> {_format_annotation(ret)}" if ret is not None else ""
    prefix = "async def" if inspect.iscoroutinefunction(fn) else "def"
    return f"{prefix} {name}({', '.join(params)}){ret_str}:"


def _format_annotation(annotation: Any) -> str:
    if annotation is None:
        return "None"
    if annotation is type(None):
        return "None"
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation)
