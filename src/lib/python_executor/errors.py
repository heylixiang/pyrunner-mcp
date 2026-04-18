from __future__ import annotations


class InterpreterError(ValueError):
    """Raised when the restricted interpreter cannot safely evaluate code."""


class BreakException(Exception):
    """Internal control-flow exception for `break`."""


class ContinueException(Exception):
    """Internal control-flow exception for `continue`."""


class ReturnException(Exception):
    """Internal control-flow exception for `return`."""

    def __init__(self, value):
        self.value = value


class ExecutionTimeoutError(Exception):
    """Raised when code execution exceeds the configured timeout."""


class FinalAnswerException(BaseException):
    """Raised when `final_answer(...)` is called inside interpreted code."""

    def __init__(self, value):
        self.value = value
