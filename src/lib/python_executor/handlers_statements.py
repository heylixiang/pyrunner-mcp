from __future__ import annotations

import ast
from collections.abc import Awaitable, Callable
from importlib import import_module
from typing import Any

from .constants import MAX_WHILE_ITERATIONS
from .errors import BreakException, ContinueException, InterpreterError, ReturnException
from .models import EvaluationContext
from .runtime import FinalAnswerTool, run_coroutine_sync
from .security import check_import_authorized, get_safe_module

NodeEvaluator = Callable[[ast.AST, EvaluationContext], Awaitable[Any]]


async def set_value(target: ast.AST, value: Any, ctx: EvaluationContext, evaluate: NodeEvaluator) -> None:
    if isinstance(target, ast.Name):
        if target.id in ctx.static_tools:
            raise InterpreterError(f"Cannot assign to name '{target.id}': doing this would erase the existing tool!")
        ctx.state[target.id] = value
        return

    if isinstance(target, (ast.Tuple, ast.List)):
        if not hasattr(value, "__iter__") or isinstance(value, (str, bytes)):
            raise InterpreterError("Cannot unpack non-iterable value")
        unpacked = list(value)
        if len(target.elts) != len(unpacked):
            raise InterpreterError("Cannot unpack value of wrong size")
        for item, item_value in zip(target.elts, unpacked):
            await set_value(item, item_value, ctx, evaluate)
        return

    if isinstance(target, ast.Subscript):
        obj = await evaluate(target.value, ctx)
        key = await evaluate(target.slice, ctx)
        obj[key] = value
        return

    if isinstance(target, ast.Attribute):
        obj = await evaluate(target.value, ctx)
        setattr(obj, target.attr, value)
        return

    raise InterpreterError(f"Assignment to {type(target).__name__} is not supported")


async def evaluate_assign(assign: ast.Assign, ctx: EvaluationContext, evaluate: NodeEvaluator) -> Any:
    result = await evaluate(assign.value, ctx)
    for target in assign.targets:
        await set_value(target, result, ctx, evaluate)
    return result


async def evaluate_annassign(annassign: ast.AnnAssign, ctx: EvaluationContext, evaluate: NodeEvaluator) -> Any:
    if annassign.value is None:
        return None
    value = await evaluate(annassign.value, ctx)
    await set_value(annassign.target, value, ctx, evaluate)
    return value


async def evaluate_augassign(expression: ast.AugAssign, ctx: EvaluationContext, evaluate: NodeEvaluator) -> Any:
    async def get_current_value(target: ast.AST) -> Any:
        if isinstance(target, ast.Name):
            return ctx.state.get(target.id, 0)
        if isinstance(target, ast.Subscript):
            obj = await evaluate(target.value, ctx)
            key = await evaluate(target.slice, ctx)
            return obj[key]
        if isinstance(target, ast.Attribute):
            obj = await evaluate(target.value, ctx)
            return getattr(obj, target.attr)
        if isinstance(target, ast.Tuple):
            return tuple([await get_current_value(element) for element in target.elts])
        if isinstance(target, ast.List):
            return [await get_current_value(element) for element in target.elts]
        raise InterpreterError(f"AugAssign not supported for {type(target).__name__} targets.")

    current_value = await get_current_value(expression.target)
    value_to_add = await evaluate(expression.value, ctx)

    if isinstance(expression.op, ast.Add):
        if isinstance(current_value, list):
            if not isinstance(value_to_add, list):
                raise InterpreterError(f"Cannot add non-list value {value_to_add} to a list.")
            current_value += value_to_add
        else:
            current_value += value_to_add
    elif isinstance(expression.op, ast.Sub):
        current_value -= value_to_add
    elif isinstance(expression.op, ast.Mult):
        current_value *= value_to_add
    elif isinstance(expression.op, ast.Div):
        current_value /= value_to_add
    elif isinstance(expression.op, ast.Mod):
        current_value %= value_to_add
    elif isinstance(expression.op, ast.Pow):
        current_value **= value_to_add
    elif isinstance(expression.op, ast.FloorDiv):
        current_value //= value_to_add
    elif isinstance(expression.op, ast.BitAnd):
        current_value &= value_to_add
    elif isinstance(expression.op, ast.BitOr):
        current_value |= value_to_add
    elif isinstance(expression.op, ast.BitXor):
        current_value ^= value_to_add
    elif isinstance(expression.op, ast.LShift):
        current_value <<= value_to_add
    elif isinstance(expression.op, ast.RShift):
        current_value >>= value_to_add
    else:
        raise InterpreterError(f"Operation {type(expression.op).__name__} is not supported.")

    await set_value(expression.target, current_value, ctx, evaluate)
    return current_value


async def evaluate_if(if_statement: ast.If, ctx: EvaluationContext, evaluate: NodeEvaluator) -> Any:
    result = None
    body = if_statement.body if await evaluate(if_statement.test, ctx) else if_statement.orelse
    for line in body:
        line_result = await evaluate(line, ctx)
        if line_result is not None:
            result = line_result
    return result


async def evaluate_for(for_loop: ast.For, ctx: EvaluationContext, evaluate: NodeEvaluator) -> Any:
    result = None
    iterator = await evaluate(for_loop.iter, ctx)
    for counter in iterator:
        await set_value(for_loop.target, counter, ctx, evaluate)
        for node in for_loop.body:
            try:
                line_result = await evaluate(node, ctx)
                if line_result is not None:
                    result = line_result
            except BreakException:
                return result
            except ContinueException:
                break
    return result


async def evaluate_async_for(for_loop: ast.AsyncFor, ctx: EvaluationContext, evaluate: NodeEvaluator) -> Any:
    from .handlers_expressions import _iterate_values

    result = None
    iterator = await evaluate(for_loop.iter, ctx)
    async for counter in _iterate_values(iterator, is_async=True):
        await set_value(for_loop.target, counter, ctx, evaluate)
        for node in for_loop.body:
            try:
                line_result = await evaluate(node, ctx)
                if line_result is not None:
                    result = line_result
            except BreakException:
                return result
            except ContinueException:
                break
    return result


async def evaluate_while(while_loop: ast.While, ctx: EvaluationContext, evaluate: NodeEvaluator) -> None:
    iterations = 0
    while await evaluate(while_loop.test, ctx):
        for node in while_loop.body:
            try:
                await evaluate(node, ctx)
            except BreakException:
                return None
            except ContinueException:
                break
        iterations += 1
        if iterations > MAX_WHILE_ITERATIONS:
            raise InterpreterError(f"Maximum number of {MAX_WHILE_ITERATIONS} iterations in While loop exceeded")
    return None


async def _execute_function_body(
    func_def: ast.FunctionDef | ast.AsyncFunctionDef,
    ctx: EvaluationContext,
    evaluate: NodeEvaluator,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    func_state = ctx.state.copy()
    arg_names = [arg.arg for arg in func_def.args.args]
    default_values = [await evaluate(default, ctx) for default in func_def.args.defaults]
    defaults = dict(zip(arg_names[-len(default_values):], default_values))

    for name, value in zip(arg_names, args):
        func_state[name] = value

    for name, value in kwargs.items():
        func_state[name] = value

    if func_def.args.vararg:
        func_state[func_def.args.vararg.arg] = args[len(arg_names):]

    if func_def.args.kwarg:
        func_state[func_def.args.kwarg.arg] = {
            key: value for key, value in kwargs.items() if key not in arg_names
        }

    for name, value in defaults.items():
        func_state.setdefault(name, value)

    if func_def.args.args and func_def.args.args[0].arg == "self" and args:
        func_state["self"] = args[0]
        func_state["__class__"] = args[0].__class__

    function_ctx = ctx.with_state(func_state)
    result = None
    try:
        for stmt in func_def.body:
            result = await evaluate(stmt, function_ctx)
    except ReturnException as exc:
        result = exc.value

    if func_def.name == "__init__":
        return None
    return result


def create_function(func_def: ast.FunctionDef, ctx: EvaluationContext, evaluate: NodeEvaluator) -> Callable:
    source_code = ast.unparse(func_def)

    def new_func(*args: Any, **kwargs: Any) -> Any:
        return run_coroutine_sync(_execute_function_body(func_def, ctx, evaluate, args, kwargs))

    new_func.__ast__ = func_def
    new_func.__source__ = source_code
    new_func.__name__ = func_def.name
    return new_func


def create_async_function(
    func_def: ast.AsyncFunctionDef,
    ctx: EvaluationContext,
    evaluate: NodeEvaluator,
) -> Callable:
    source_code = ast.unparse(func_def)

    async def new_func(*args: Any, **kwargs: Any) -> Any:
        return await _execute_function_body(func_def, ctx, evaluate, args, kwargs)

    new_func.__ast__ = func_def
    new_func.__source__ = source_code
    new_func.__name__ = func_def.name
    return new_func


async def evaluate_function_def(func_def: ast.FunctionDef, ctx: EvaluationContext, evaluate: NodeEvaluator) -> Callable:
    ctx.custom_tools[func_def.name] = create_function(func_def, ctx, evaluate)
    return ctx.custom_tools[func_def.name]


async def evaluate_async_function_def(
    func_def: ast.AsyncFunctionDef,
    ctx: EvaluationContext,
    evaluate: NodeEvaluator,
) -> Callable:
    ctx.custom_tools[func_def.name] = create_async_function(func_def, ctx, evaluate)
    return ctx.custom_tools[func_def.name]


async def evaluate_class_def(class_def: ast.ClassDef, ctx: EvaluationContext, evaluate: NodeEvaluator) -> type:
    class_name = class_def.name
    bases = [await evaluate(base, ctx) for base in class_def.bases]
    bases_tuple = tuple(bases)

    metaclass = type
    for base in bases:
        base_metaclass = type(base)
        if base_metaclass is not type:
            metaclass = base_metaclass
            break

    class_dict = metaclass.__prepare__(class_name, bases_tuple) if hasattr(metaclass, "__prepare__") else {}

    def class_ctx() -> EvaluationContext:
        return ctx.with_state({**ctx.state, **class_dict})

    for index, stmt in enumerate(class_def.body):
        if isinstance(stmt, ast.FunctionDef):
            class_dict[stmt.name] = await evaluate_function_def(stmt, ctx, evaluate)
        elif isinstance(stmt, ast.AsyncFunctionDef):
            class_dict[stmt.name] = await evaluate_async_function_def(stmt, ctx, evaluate)
        elif isinstance(stmt, ast.AnnAssign):
            value = await evaluate(stmt.value, class_ctx()) if stmt.value else None
            target = stmt.target
            if isinstance(target, ast.Name):
                annotation = await evaluate(stmt.annotation, class_ctx())
                class_dict.setdefault("__annotations__", {})[target.id] = annotation
                if stmt.value:
                    class_dict[target.id] = value
            elif isinstance(target, ast.Attribute):
                obj = await evaluate(target.value, class_ctx())
                if stmt.value:
                    setattr(obj, target.attr, value)
            elif isinstance(target, ast.Subscript):
                container = await evaluate(target.value, class_ctx())
                key = await evaluate(target.slice, class_ctx())
                if stmt.value:
                    container[key] = value
            else:
                raise InterpreterError(f"Unsupported AnnAssign target in class body: {type(target).__name__}")
        elif isinstance(stmt, ast.Assign):
            value = await evaluate(stmt.value, class_ctx())
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    class_dict[target.id] = value
                elif isinstance(target, ast.Attribute):
                    obj = await evaluate(target.value, class_ctx())
                    setattr(obj, target.attr, value)
                else:
                    raise InterpreterError(f"Unsupported assignment target in class body: {type(target).__name__}")
        elif isinstance(stmt, ast.Pass):
            continue
        elif (
            isinstance(stmt, ast.Expr)
            and index == 0
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        ):
            class_dict["__doc__"] = stmt.value.value
        else:
            raise InterpreterError(f"Unsupported statement in class body: {stmt.__class__.__name__}")

    new_class = metaclass(class_name, bases_tuple, class_dict)
    ctx.state[class_name] = new_class
    return new_class


async def evaluate_try(try_node: ast.Try, ctx: EvaluationContext, evaluate: NodeEvaluator) -> None:
    try:
        for stmt in try_node.body:
            await evaluate(stmt, ctx)
    except Exception as exc:
        matched = False
        for handler in try_node.handlers:
            expected_type = await evaluate(handler.type, ctx) if handler.type is not None else None
            if expected_type is None or isinstance(exc, expected_type):
                matched = True
                if handler.name:
                    ctx.state[handler.name] = exc
                for stmt in handler.body:
                    await evaluate(stmt, ctx)
                break
        if not matched:
            raise
    else:
        for stmt in try_node.orelse:
            await evaluate(stmt, ctx)
    finally:
        for stmt in try_node.finalbody:
            await evaluate(stmt, ctx)


async def evaluate_raise(raise_node: ast.Raise, ctx: EvaluationContext, evaluate: NodeEvaluator) -> None:
    exc = await evaluate(raise_node.exc, ctx) if raise_node.exc is not None else None
    cause = await evaluate(raise_node.cause, ctx) if raise_node.cause is not None else None
    if exc is None:
        raise InterpreterError("Re-raise is not supported without an active exception")
    if cause is not None:
        raise exc from cause
    raise exc


async def evaluate_assert(assert_node: ast.Assert, ctx: EvaluationContext, evaluate: NodeEvaluator) -> None:
    if await evaluate(assert_node.test, ctx):
        return None
    if assert_node.msg:
        raise AssertionError(await evaluate(assert_node.msg, ctx))
    raise AssertionError(f"Assertion failed: {ast.unparse(assert_node.test)}")


async def evaluate_with(with_node: ast.With, ctx: EvaluationContext, evaluate: NodeEvaluator) -> None:
    contexts = []
    for item in with_node.items:
        context_expr = await evaluate(item.context_expr, ctx)
        enter_result = context_expr.__enter__()
        contexts.append(context_expr)
        if item.optional_vars:
            await set_value(item.optional_vars, enter_result, ctx, evaluate)

    try:
        for stmt in with_node.body:
            await evaluate(stmt, ctx)
    except Exception as exc:
        exc_info = (type(exc), exc, exc.__traceback__)
        for context in reversed(contexts):
            try:
                if context.__exit__(*exc_info):
                    exc_info = (None, None, None)
            except Exception as exit_exc:
                exc_info = (type(exit_exc), exit_exc, exit_exc.__traceback__)
        if exc_info[1] is not None:
            raise exc_info[1].with_traceback(exc_info[2])
    else:
        for context in reversed(contexts):
            context.__exit__(None, None, None)


async def evaluate_async_with(with_node: ast.AsyncWith, ctx: EvaluationContext, evaluate: NodeEvaluator) -> None:
    contexts = []
    for item in with_node.items:
        context_expr = await evaluate(item.context_expr, ctx)
        if not hasattr(context_expr, "__aenter__") or not hasattr(context_expr, "__aexit__"):
            raise InterpreterError(f"Object {context_expr!r} is not an async context manager")
        enter_result = await context_expr.__aenter__()
        contexts.append(context_expr)
        if item.optional_vars:
            await set_value(item.optional_vars, enter_result, ctx, evaluate)

    try:
        for stmt in with_node.body:
            await evaluate(stmt, ctx)
    except Exception as exc:
        exc_info = (type(exc), exc, exc.__traceback__)
        for context in reversed(contexts):
            try:
                if await context.__aexit__(*exc_info):
                    exc_info = (None, None, None)
            except Exception as exit_exc:
                exc_info = (type(exit_exc), exit_exc, exit_exc.__traceback__)
        if exc_info[1] is not None:
            raise exc_info[1].with_traceback(exc_info[2])
    else:
        for context in reversed(contexts):
            await context.__aexit__(None, None, None)


async def evaluate_import(expression: ast.AST, ctx: EvaluationContext, evaluate: NodeEvaluator) -> None:
    if isinstance(expression, ast.Import):
        for alias in expression.names:
            if not check_import_authorized(alias.name, ctx.authorized_imports):
                raise InterpreterError(
                    f"Import of {alias.name} is not allowed. Authorized imports are: {ctx.authorized_imports}"
                )
            raw_module = import_module(alias.name)
            ctx.state[alias.asname or alias.name] = get_safe_module(raw_module, ctx.authorized_imports)
        return None

    if not isinstance(expression, ast.ImportFrom) or expression.module is None:
        raise InterpreterError("Relative imports are not supported.")

    if expression.level != 0:
        raise InterpreterError("Relative imports are not supported.")

    if not check_import_authorized(expression.module, ctx.authorized_imports):
        raise InterpreterError(
            f"Import from {expression.module} is not allowed. Authorized imports are: {ctx.authorized_imports}"
        )

    raw_module = import_module(expression.module)
    module = get_safe_module(raw_module, ctx.authorized_imports)

    if expression.names[0].name == "*":
        if hasattr(module, "__all__"):
            for name in module.__all__:
                ctx.state[name] = getattr(module, name)
        else:
            for name in dir(module):
                if not name.startswith("_"):
                    ctx.state[name] = getattr(module, name)
        return None

    for alias in expression.names:
        if not hasattr(module, alias.name):
            raise InterpreterError(f"Module {expression.module} has no attribute {alias.name}")
        ctx.state[alias.asname or alias.name] = getattr(module, alias.name)
    return None


async def evaluate_delete(delete_node: ast.Delete, ctx: EvaluationContext, evaluate: NodeEvaluator) -> None:
    for target in delete_node.targets:
        if isinstance(target, ast.Name):
            if target.id not in ctx.state:
                raise InterpreterError(f"Cannot delete name '{target.id}': name is not defined")
            del ctx.state[target.id]
        elif isinstance(target, ast.Subscript):
            obj = await evaluate(target.value, ctx)
            index = await evaluate(target.slice, ctx)
            try:
                del obj[index]
            except (TypeError, KeyError, IndexError) as exc:
                raise InterpreterError(f"Cannot delete index/key: {exc}") from exc
        else:
            raise InterpreterError(f"Deletion of {type(target).__name__} targets is not supported")


def wrap_final_answer_tool(tool: Callable[..., Any]) -> FinalAnswerTool:
    return FinalAnswerTool(tool)
