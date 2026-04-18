from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .constants import MAX_OPERATIONS
from .errors import InterpreterError


class Tool:
    ...


@dataclass
class PrintContainer:
    value: str = ""

    def append(self, text: str) -> "PrintContainer":
        self.value += text
        return self

    def __iadd__(self, other) -> "PrintContainer":
        self.value += str(other)
        return self

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"PrintContainer({self.value})"

    def __len__(self) -> int:
        return len(self.value)


@dataclass
class CodeOutput:
    output: Any
    logs: str
    is_final_answer: bool


@dataclass
class EvaluationContext:
    state: dict[str, Any]
    static_tools: dict[str, Callable]
    custom_tools: dict[str, Callable]
    authorized_imports: list[str]

    def with_state(self, state: dict[str, Any]) -> "EvaluationContext":
        return EvaluationContext(
            state=state,
            static_tools=self.static_tools,
            custom_tools=self.custom_tools,
            authorized_imports=self.authorized_imports,
        )

    def increment_operations(self) -> None:
        operations = self.state.setdefault("_operations_count", {"counter": 0})
        if operations["counter"] >= MAX_OPERATIONS:
            raise InterpreterError(
                f"Reached the max number of operations of {MAX_OPERATIONS}. "
                "Maybe there is an infinite loop somewhere in the code, or you're just asking too many calculations."
            )
        operations["counter"] += 1

    def append_print(self, *args: Any) -> None:
        self.state.setdefault("_print_outputs", PrintContainer())
        self.state["_print_outputs"] += " ".join(map(str, args)) + "\n"
