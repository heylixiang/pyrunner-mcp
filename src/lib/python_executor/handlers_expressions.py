from __future__ import annotations

import ast
import builtins
import difflib
import inspect
from collections.abc import AsyncIterable, Awaitable, Callable, Mapping
from typing import Any

from .constants import ALLOWED_DUNDER_METHODS, ERRORS
from .errors import FinalAnswerException, InterpreterError
from .models import EvaluationContext
from .runtime import is_final_answer_tool, run_coroutine_sync
from .security import check_safer_result, safer_func

NodeEvaluator = Callable[[ast.AST, EvaluationContext], Awaitable[Any]]
AssignmentHandler = Callable[[ast.AST, Any, EvaluationContext, NodeEvaluator], Awaitable[None]]


async def evaluate_attribute(expression: ast.Attribute, ctx: EvaluationContext, evaluate: NodeEvaluator) -> Any:
    if expression.attr.startswith("__") and expression.attr.endswith("__"):
        raise InterpreterError(f"Forbidden access to dunder attribute: {expression.attr}")
    value = await evaluate(expression.value, ctx)
    return getattr(value, expression.attr)


async def evaluate_unaryop(expression: ast.UnaryOp, ctx: EvaluationContext, evaluate: NodeEvaluator) -> Any:
    operand = await evaluate(expression.operand, ctx)
    if isinstance(expression.op, ast.USub):
        return -operand
    if isinstance(expression.op, ast.UAdd):
        return operand
    if isinstance(expression.op, ast.Not):
        return not operand
    if isinstance(expression.op, ast.Invert):
        return ~operand
    raise InterpreterError(f"Unary operation {expression.op.__class__.__name__} is not supported.")


async def evaluate_lambda(lambda_expression: ast.Lambda, ctx: EvaluationContext, evaluate: NodeEvaluator) -> Callable:
    args = [arg.arg for arg in lambda_expression.args.args]

    def lambda_func(*values: Any) -> Any:
        new_state = ctx.state.copy()
        for arg, value in zip(args, values):
            new_state[arg] = value
        return run_coroutine_sync(evaluate(lambda_expression.body, ctx.with_state(new_state)))

    return lambda_func


async def evaluate_boolop(node: ast.BoolOp, ctx: EvaluationContext, evaluate: NodeEvaluator) -> Any:
    is_short_circuit_value = (lambda value: not value) if isinstance(node.op, ast.And) else bool
    result = None
    for value in node.values:
        result = await evaluate(value, ctx)
        if is_short_circuit_value(result):
            return result
    return result


async def evaluate_binop(binop: ast.BinOp, ctx: EvaluationContext, evaluate: NodeEvaluator) -> Any:
    left_val = await evaluate(binop.left, ctx)
    right_val = await evaluate(binop.right, ctx)

    if isinstance(binop.op, ast.Add):
        return left_val + right_val
    if isinstance(binop.op, ast.Sub):
        return left_val - right_val
    if isinstance(binop.op, ast.Mult):
        return left_val * right_val
    if isinstance(binop.op, ast.Div):
        return left_val / right_val
    if isinstance(binop.op, ast.Mod):
        return left_val % right_val
    if isinstance(binop.op, ast.Pow):
        return left_val**right_val
    if isinstance(binop.op, ast.FloorDiv):
        return left_val // right_val
    if isinstance(binop.op, ast.BitAnd):
        return left_val & right_val
    if isinstance(binop.op, ast.BitOr):
        return left_val | right_val
    if isinstance(binop.op, ast.BitXor):
        return left_val ^ right_val
    if isinstance(binop.op, ast.LShift):
        return left_val << right_val
    if isinstance(binop.op, ast.RShift):
        return left_val >> right_val
    raise InterpreterError(f"Binary operation {type(binop.op).__name__} is not supported.")


async def evaluate_subscript(subscript: ast.Subscript, ctx: EvaluationContext, evaluate: NodeEvaluator) -> Any:
    index = await evaluate(subscript.slice, ctx)
    value = await evaluate(subscript.value, ctx)
    try:
        return value[index]
    except (KeyError, IndexError, TypeError) as exc:
        message = f"Could not index {value} with '{index}': {type(exc).__name__}: {exc}"
        if isinstance(index, str) and isinstance(value, Mapping):
            close_matches = difflib.get_close_matches(index, list(value.keys()))
            if close_matches:
                message += f". Maybe you meant one of these indexes instead: {close_matches}"
        raise InterpreterError(message) from exc


async def evaluate_name(name: ast.Name, ctx: EvaluationContext, evaluate: NodeEvaluator) -> Any:
    if name.id in ctx.state:
        return ctx.state[name.id]
    if name.id in ctx.static_tools:
        return safer_func(ctx.static_tools[name.id], ctx)
    if name.id in ctx.custom_tools:
        return ctx.custom_tools[name.id]
    if name.id in ERRORS:
        return ERRORS[name.id]

    close_matches = difflib.get_close_matches(name.id, list(ctx.state.keys()))
    if close_matches:
        return ctx.state[close_matches[0]]
    raise InterpreterError(f"The variable `{name.id}` is not defined.")


async def evaluate_condition(condition: ast.Compare, ctx: EvaluationContext, evaluate: NodeEvaluator) -> bool | object:
    result = True
    left = await evaluate(condition.left, ctx)

    for index, (operator, comparator) in enumerate(zip(condition.ops, condition.comparators)):
        operator_type = type(operator)
        right = await evaluate(comparator, ctx)
        if operator_type is ast.Eq:
            current_result = left == right
        elif operator_type is ast.NotEq:
            current_result = left != right
        elif operator_type is ast.Lt:
            current_result = left < right
        elif operator_type is ast.LtE:
            current_result = left <= right
        elif operator_type is ast.Gt:
            current_result = left > right
        elif operator_type is ast.GtE:
            current_result = left >= right
        elif operator_type is ast.Is:
            current_result = left is right
        elif operator_type is ast.IsNot:
            current_result = left is not right
        elif operator_type is ast.In:
            current_result = left in right
        elif operator_type is ast.NotIn:
            current_result = left not in right
        else:
            raise InterpreterError(f"Unsupported comparison operator: {operator_type}")

        if current_result is False:
            return False

        result = current_result if index == 0 else (result and current_result)
        left = right

    return result


async def _iterate_values(iter_value: Any, *, is_async: bool):
    if is_async:
        if not isinstance(iter_value, AsyncIterable):
            raise InterpreterError(f"Object {iter_value!r} is not async iterable")
        async for item in iter_value:
            yield item
        return

    for item in iter_value:
        yield item


async def _passes_filters(
    if_clauses: list[ast.expr],
    inner_ctx: EvaluationContext,
    evaluate: NodeEvaluator,
) -> bool:
    for if_clause in if_clauses:
        if not await evaluate(if_clause, inner_ctx):
            return False
    return True


async def _evaluate_comprehensions(
    comprehensions: list[ast.comprehension],
    evaluate_element: Callable[[EvaluationContext], Awaitable[Any]],
    ctx: EvaluationContext,
    evaluate: NodeEvaluator,
    set_value: AssignmentHandler,
):
    if not comprehensions:
        yield await evaluate_element(ctx)
        return

    comprehension = comprehensions[0]
    iter_value = await evaluate(comprehension.iter, ctx)
    async for value in _iterate_values(iter_value, is_async=bool(comprehension.is_async)):
        new_state = ctx.state.copy()
        inner_ctx = ctx.with_state(new_state)
        await set_value(comprehension.target, value, inner_ctx, evaluate)
        if await _passes_filters(comprehension.ifs, inner_ctx, evaluate):
            async for item in _evaluate_comprehensions(
                comprehensions[1:],
                evaluate_element,
                inner_ctx,
                evaluate,
                set_value,
            ):
                yield item


async def evaluate_listcomp(
    listcomp: ast.ListComp,
    ctx: EvaluationContext,
    evaluate: NodeEvaluator,
    set_value: AssignmentHandler,
) -> list[Any]:
    values: list[Any] = []
    async for item in _evaluate_comprehensions(
        listcomp.generators,
        lambda inner_ctx: evaluate(listcomp.elt, inner_ctx),
        ctx,
        evaluate,
        set_value,
    ):
        values.append(item)
    return values


async def evaluate_setcomp(
    setcomp: ast.SetComp,
    ctx: EvaluationContext,
    evaluate: NodeEvaluator,
    set_value: AssignmentHandler,
) -> set[Any]:
    values: set[Any] = set()
    async for item in _evaluate_comprehensions(
        setcomp.generators,
        lambda inner_ctx: evaluate(setcomp.elt, inner_ctx),
        ctx,
        evaluate,
        set_value,
    ):
        values.add(item)
    return values


async def evaluate_dictcomp(
    dictcomp: ast.DictComp,
    ctx: EvaluationContext,
    evaluate: NodeEvaluator,
    set_value: AssignmentHandler,
) -> dict[Any, Any]:
    values: dict[Any, Any] = {}

    async def evaluate_item(inner_ctx: EvaluationContext) -> tuple[Any, Any]:
        return (
            await evaluate(dictcomp.key, inner_ctx),
            await evaluate(dictcomp.value, inner_ctx),
        )

    async for key, value in _evaluate_comprehensions(
        dictcomp.generators,
        evaluate_item,
        ctx,
        evaluate,
        set_value,
    ):
        values[key] = value
    return values


async def evaluate_generatorexp(
    genexp: ast.GeneratorExp,
    ctx: EvaluationContext,
    evaluate: NodeEvaluator,
    set_value: AssignmentHandler,
):
    values: list[Any] = []
    async for item in _evaluate_comprehensions(
        genexp.generators,
        lambda inner_ctx: evaluate(genexp.elt, inner_ctx),
        ctx,
        evaluate,
        set_value,
    ):
        values.append(item)
    return iter(values)


async def evaluate_await(expression: ast.Await, ctx: EvaluationContext, evaluate: NodeEvaluator) -> Any:
    awaitable = await evaluate(expression.value, ctx)
    if not inspect.isawaitable(awaitable):
        raise InterpreterError("Await expects an awaitable value.")
    return await awaitable


async def evaluate_call(call: ast.Call, ctx: EvaluationContext, evaluate: NodeEvaluator) -> Any:
    if not isinstance(call.func, (ast.Call, ast.Lambda, ast.Attribute, ast.Name, ast.Subscript)):
        raise InterpreterError(f"This is not a correct function: {call.func}).")

    func = None
    func_name = None

    if isinstance(call.func, ast.Call):
        func = await evaluate(call.func, ctx)
    elif isinstance(call.func, ast.Lambda):
        func = await evaluate(call.func, ctx)
    elif isinstance(call.func, ast.Attribute):
        obj = await evaluate(call.func.value, ctx)
        func_name = call.func.attr
        if not hasattr(obj, func_name):
            raise InterpreterError(f"Object {obj} has no attribute {func_name}")
        func = getattr(obj, func_name)
        check_safer_result(func, ctx.static_tools, ctx.authorized_imports)
    elif isinstance(call.func, ast.Name):
        func_name = call.func.id
        if func_name == "super":
            func = super
        elif func_name in ctx.state:
            func = ctx.state[func_name]
        elif func_name in ctx.static_tools:
            func = ctx.static_tools[func_name]
        elif func_name in ctx.custom_tools:
            func = ctx.custom_tools[func_name]
        elif func_name in ERRORS:
            func = ERRORS[func_name]
        else:
            raise InterpreterError(
                f"Forbidden function evaluation: '{call.func.id}' is not among the explicitly allowed tools "
                "or defined/imported in the preceding code"
            )
    elif isinstance(call.func, ast.Subscript):
        func = await evaluate(call.func, ctx)
        if not callable(func):
            raise InterpreterError(f"This is not a correct function: {call.func}).")

    args: list[Any] = []
    for arg in call.args:
        if isinstance(arg, ast.Starred):
            args.extend(await evaluate(arg.value, ctx))
        else:
            args.append(await evaluate(arg, ctx))

    kwargs: dict[str, Any] = {}
    for keyword in call.keywords:
        if keyword.arg is None:
            starred_dict = await evaluate(keyword.value, ctx)
            if not isinstance(starred_dict, dict):
                raise InterpreterError(f"Cannot unpack non-dict value in **kwargs: {type(starred_dict).__name__}")
            kwargs.update(starred_dict)
        else:
            kwargs[keyword.arg] = await evaluate(keyword.value, ctx)

    if func_name == "super":
        if not args:
            if "__class__" in ctx.state and "self" in ctx.state:
                return super(ctx.state["__class__"], ctx.state["self"])
            raise InterpreterError("super() needs at least one argument")

        cls = args[0]
        if not isinstance(cls, type):
            raise InterpreterError("super() argument 1 must be type")
        if len(args) == 1:
            return super(cls)
        if len(args) == 2:
            return super(cls, args[1])
        raise InterpreterError("super() takes at most 2 arguments")

    if func_name == "print":
        ctx.append_print(*args)
        return None

    if inspect.getmodule(func) == builtins and inspect.isbuiltin(func) and func not in ctx.static_tools.values():
        raise InterpreterError(
            f"Invoking a builtin function that has not been explicitly added as a tool is not allowed ({func_name})."
        )

    if (
        hasattr(func, "__name__")
        and func.__name__.startswith("__")
        and func.__name__.endswith("__")
        and func.__name__ not in ctx.static_tools
        and func.__name__ not in ALLOWED_DUNDER_METHODS
    ):
        raise InterpreterError(f"Forbidden call to dunder function: {func.__name__}")

    result = func(*args, **kwargs)
    if is_final_answer_tool(func):
        if inspect.isawaitable(result):
            result = await result
        raise FinalAnswerException(result)
    return result
