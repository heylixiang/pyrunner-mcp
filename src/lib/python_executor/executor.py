from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from importlib.util import find_spec
from typing import Any

from .utils import BASE_BUILTIN_MODULES
from .constants import BASE_PYTHON_TOOLS, DEFAULT_MAX_LEN_OUTPUT, MAX_EXECUTION_TIME_SECONDS
from .errors import InterpreterError
from .evaluator import evaluate_python_code, evaluate_python_code_async
from .models import CodeOutput, Tool


class PythonExecutor(ABC):
    @abstractmethod
    def send_tools(self, tools: dict[str, Tool]) -> None: ...

    @abstractmethod
    def send_variables(self, variables: dict[str, Any]) -> None: ...

    @abstractmethod
    def __call__(self, code_action: str) -> CodeOutput: ...

    async def execute_async(self, code_action: str) -> CodeOutput:
        raise NotImplementedError


class LocalPythonExecutor(PythonExecutor):
    """
    Execute Python code locally with restricted imports and tools.
    """

    def __init__(
        self,
        additional_authorized_imports: list[str] | None = None,
        max_print_outputs_length: int | None = None,
        additional_functions: dict[str, Callable] | None = None,
        timeout_seconds: int | None = MAX_EXECUTION_TIME_SECONDS,
    ):
        self.custom_tools: dict[str, Callable] = {}
        self.state = {"__name__": "__main__"}
        self.max_print_outputs_length = max_print_outputs_length or DEFAULT_MAX_LEN_OUTPUT
        self.additional_authorized_imports = additional_authorized_imports or []
        self.authorized_imports = sorted(set(BASE_BUILTIN_MODULES) | set(self.additional_authorized_imports))
        self.additional_functions = additional_functions or {}
        self.timeout_seconds = timeout_seconds
        self._check_authorized_imports_are_installed()
        self.static_tools: dict[str, Callable] = self._build_static_tools()

    def _build_static_tools(self, tools: dict[str, Tool] | None = None) -> dict[str, Callable]:
        return {**(tools or {}), **BASE_PYTHON_TOOLS.copy(), **self.additional_functions}

    def _check_authorized_imports_are_installed(self) -> None:
        missing_modules = [
            base_module
            for imp in self.authorized_imports
            if imp != "*" and find_spec(base_module := imp.split(".")[0]) is None
        ]
        if missing_modules:
            raise InterpreterError(
                f"Non-installed authorized modules: {', '.join(missing_modules)}. "
                "Please install these modules or remove them from the authorized imports list."
            )

    def __call__(self, code_action: str) -> CodeOutput:
        output, is_final_answer = evaluate_python_code(
            code_action,
            static_tools=self.static_tools,
            custom_tools=self.custom_tools,
            state=self.state,
            authorized_imports=self.authorized_imports,
            max_print_outputs_length=self.max_print_outputs_length,
            timeout_seconds=self.timeout_seconds,
        )
        return CodeOutput(output=output, logs=str(self.state["_print_outputs"]), is_final_answer=is_final_answer)

    async def execute_async(self, code_action: str) -> CodeOutput:
        output, is_final_answer = await evaluate_python_code_async(
            code_action,
            static_tools=self.static_tools,
            custom_tools=self.custom_tools,
            state=self.state,
            authorized_imports=self.authorized_imports,
            max_print_outputs_length=self.max_print_outputs_length,
            timeout_seconds=self.timeout_seconds,
        )
        return CodeOutput(output=output, logs=str(self.state["_print_outputs"]), is_final_answer=is_final_answer)

    def send_variables(self, variables: dict[str, Any]) -> None:
        self.state.update(variables)

    def send_tools(self, tools: dict[str, Tool]) -> None:
        self.static_tools = self._build_static_tools(tools)
