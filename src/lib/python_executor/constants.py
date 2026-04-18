from __future__ import annotations

import builtins
import math
from collections.abc import Callable

from .errors import InterpreterError

DEFAULT_MAX_LEN_OUTPUT = 50_000
MAX_OPERATIONS = 10_000_000
MAX_WHILE_ITERATIONS = 1_000_000
MAX_EXECUTION_TIME_SECONDS = 30
ALLOWED_DUNDER_METHODS = {"__init__", "__str__", "__repr__"}


def custom_print(*args):
    return None


def nodunder_getattr(obj, name, default=None):
    if name.startswith("__") and name.endswith("__"):
        raise InterpreterError(f"Forbidden access to dunder attribute: {name}")
    return getattr(obj, name, default)


BASE_PYTHON_TOOLS: dict[str, Callable] = {
    "print": custom_print,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "callable": callable,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "reversed": reversed,
    "sorted": sorted,
    "all": all,
    "any": any,
    "map": map,
    "filter": filter,
    "next": next,
    "iter": iter,
    "divmod": divmod,
    "len": len,
    "sum": sum,
    "max": max,
    "min": min,
    "abs": abs,
    "round": round,
    "pow": pow,
    "ord": ord,
    "chr": chr,
    "float": float,
    "int": int,
    "bool": bool,
    "str": str,
    "complex": complex,
    "set": set,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "type": type,
    "super": super,
    "getattr": nodunder_getattr,
    "hasattr": hasattr,
    "setattr": setattr,
    "ceil": math.ceil,
    "floor": math.floor,
    "log": math.log,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "degrees": math.degrees,
    "radians": math.radians,
    "sqrt": math.sqrt,
}

# Non-exhaustive list of dangerous modules that should not be imported casually.
# The current policy is allowlist-driven, so this list is mostly documentary.
DANGEROUS_MODULES = [
    "builtins",
    "io",
    "multiprocessing",
    "os",
    "pathlib",
    "pty",
    "shutil",
    "socket",
    "subprocess",
    "sys",
]

DANGEROUS_FUNCTIONS = [
    "builtins.compile",
    "builtins.eval",
    "builtins.exec",
    "builtins.globals",
    "builtins.locals",
    "builtins.open",
    "builtins.__import__",
    "io.open",
    "os.popen",
    "os.system",
    "posix.system",
]

ERRORS = {
    name: getattr(builtins, name)
    for name in dir(builtins)
    if isinstance(getattr(builtins, name), type) and issubclass(getattr(builtins, name), BaseException)
}
