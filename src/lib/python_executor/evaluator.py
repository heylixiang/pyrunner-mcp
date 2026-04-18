from __future__ import annotations

import ast
import asyncio
from collections.abc import Callable
from typing import Any

from . import handlers_expressions as expr_handlers
from . import handlers_statements as stmt_handlers
from .constants import DEFAULT_MAX_LEN_OUTPUT, MAX_EXECUTION_TIME_SECONDS
from .errors import (
    BreakException,
    ContinueException,
    ExecutionTimeoutError,
    FinalAnswerException,
    InterpreterError,
    ReturnException,
)
from .models import EvaluationContext, PrintContainer
from .runtime import fix_final_answer_code, run_coroutine_sync
from .security import safer_eval_async
from .utils import BASE_BUILTIN_MODULES, truncate_content

NodeEvaluator = Callable[[ast.AST, EvaluationContext], Any]


async def _evaluate_constant(expression: ast.Constant, ctx: EvaluationContext, evaluate: NodeEvaluator) -> Any:
    return expression.value


async def _evaluate_tuple(expression: ast.Tuple, ctx: EvaluationContext, evaluate: NodeEvaluator) -> tuple[Any, ...]:
    return tuple([await evaluate(element, ctx) for element in expression.elts])


async def _evaluate_list(expression: ast.List, ctx: EvaluationContext, evaluate: NodeEvaluator) -> list[Any]:
    return [await evaluate(element, ctx) for element in expression.elts]


async def _evaluate_dict(expression: ast.Dict, ctx: EvaluationContext, evaluate: NodeEvaluator) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in zip(expression.keys, expression.values):
        if key_node is None:
            unpacked = await evaluate(value_node, ctx)
            if not isinstance(unpacked, dict):
                raise InterpreterError("Cannot unpack non-dict value into a dict literal")
            result.update(unpacked)
        else:
            result[await evaluate(key_node, ctx)] = await evaluate(value_node, ctx)
    return result


async def _evaluate_expr(expression: ast.Expr, ctx: EvaluationContext, evaluate: NodeEvaluator) -> Any:
    return await evaluate(expression.value, ctx)


async def _evaluate_formatted_value(
    expression: ast.FormattedValue,
    ctx: EvaluationContext,
    evaluate: NodeEvaluator,
) -> Any:
    value = await evaluate(expression.value, ctx)
    if not expression.format_spec:
        return value
    return format(value, await evaluate(expression.format_spec, ctx))


async def _evaluate_joined_str(expression: ast.JoinedStr, ctx: EvaluationContext, evaluate: NodeEvaluator) -> str:
    return "".join([str(await evaluate(value, ctx)) for value in expression.values])


async def _evaluate_ifexp(expression: ast.IfExp, ctx: EvaluationContext, evaluate: NodeEvaluator) -> Any:
    branch = expression.body if await evaluate(expression.test, ctx) else expression.orelse
    return await evaluate(branch, ctx)


async def _evaluate_slice(expression: ast.Slice, ctx: EvaluationContext, evaluate: NodeEvaluator) -> slice:
    return slice(
        await evaluate(expression.lower, ctx) if expression.lower is not None else None,
        await evaluate(expression.upper, ctx) if expression.upper is not None else None,
        await evaluate(expression.step, ctx) if expression.step is not None else None,
    )


async def _evaluate_set(expression: ast.Set, ctx: EvaluationContext, evaluate: NodeEvaluator) -> set[Any]:
    values: set[Any] = set()
    for element in expression.elts:
        values.add(await evaluate(element, ctx))
    return values


async def _evaluate_return(expression: ast.Return, ctx: EvaluationContext, evaluate: NodeEvaluator) -> None:
    raise ReturnException(await evaluate(expression.value, ctx) if expression.value else None)


async def _evaluate_index(expression: ast.AST, ctx: EvaluationContext, evaluate: NodeEvaluator) -> Any:
    return await evaluate(expression.value, ctx)


async def _evaluate_pass(expression: ast.Pass, ctx: EvaluationContext, evaluate: NodeEvaluator) -> None:
    return None


NODE_HANDLERS: dict[type[ast.AST], Callable[..., Any]] = {
    ast.Assign: stmt_handlers.evaluate_assign,
    ast.AnnAssign: stmt_handlers.evaluate_annassign,
    ast.AsyncFor: stmt_handlers.evaluate_async_for,
    ast.AsyncFunctionDef: stmt_handlers.evaluate_async_function_def,
    ast.AsyncWith: stmt_handlers.evaluate_async_with,
    ast.Assert: stmt_handlers.evaluate_assert,
    ast.Attribute: expr_handlers.evaluate_attribute,
    ast.Await: expr_handlers.evaluate_await,
    ast.AugAssign: stmt_handlers.evaluate_augassign,
    ast.BinOp: expr_handlers.evaluate_binop,
    ast.BoolOp: expr_handlers.evaluate_boolop,
    ast.Call: expr_handlers.evaluate_call,
    ast.ClassDef: stmt_handlers.evaluate_class_def,
    ast.Compare: expr_handlers.evaluate_condition,
    ast.Constant: _evaluate_constant,
    ast.Delete: stmt_handlers.evaluate_delete,
    ast.Dict: _evaluate_dict,
    ast.DictComp: lambda expression, ctx, evaluate: expr_handlers.evaluate_dictcomp(
        expression, ctx, evaluate, stmt_handlers.set_value
    ),
    ast.Expr: _evaluate_expr,
    ast.For: stmt_handlers.evaluate_for,
    ast.FormattedValue: _evaluate_formatted_value,
    ast.FunctionDef: stmt_handlers.evaluate_function_def,
    ast.GeneratorExp: lambda expression, ctx, evaluate: expr_handlers.evaluate_generatorexp(
        expression, ctx, evaluate, stmt_handlers.set_value
    ),
    ast.If: stmt_handlers.evaluate_if,
    ast.IfExp: _evaluate_ifexp,
    ast.Import: stmt_handlers.evaluate_import,
    ast.ImportFrom: stmt_handlers.evaluate_import,
    ast.JoinedStr: _evaluate_joined_str,
    ast.Lambda: expr_handlers.evaluate_lambda,
    ast.List: _evaluate_list,
    ast.ListComp: lambda expression, ctx, evaluate: expr_handlers.evaluate_listcomp(
        expression, ctx, evaluate, stmt_handlers.set_value
    ),
    ast.Name: expr_handlers.evaluate_name,
    ast.Pass: _evaluate_pass,
    ast.Raise: stmt_handlers.evaluate_raise,
    ast.Return: _evaluate_return,
    ast.Set: _evaluate_set,
    ast.SetComp: lambda expression, ctx, evaluate: expr_handlers.evaluate_setcomp(
        expression, ctx, evaluate, stmt_handlers.set_value
    ),
    ast.Slice: _evaluate_slice,
    ast.Starred: lambda expression, ctx, evaluate: evaluate(expression.value, ctx),
    ast.Subscript: expr_handlers.evaluate_subscript,
    ast.Try: stmt_handlers.evaluate_try,
    ast.Tuple: _evaluate_tuple,
    ast.UnaryOp: expr_handlers.evaluate_unaryop,
    ast.While: stmt_handlers.evaluate_while,
    ast.With: stmt_handlers.evaluate_with,
}

INDEX_NODE = getattr(ast, "Index", None)
if INDEX_NODE is not None:
    NODE_HANDLERS[INDEX_NODE] = _evaluate_index


@safer_eval_async
async def evaluate_ast(expression: ast.AST, ctx: EvaluationContext):
    ctx.increment_operations()

    if isinstance(expression, ast.Break):
        raise BreakException()
    if isinstance(expression, ast.Continue):
        raise ContinueException()

    handler = NODE_HANDLERS.get(type(expression))
    if handler is None:
        raise InterpreterError(f"{expression.__class__.__name__} is not supported.")

    return await handler(expression, ctx, evaluate_ast)


def _finalize_print_outputs(state: dict[str, Any], max_print_outputs_length: int) -> None:
    state["_print_outputs"].value = truncate_content(str(state["_print_outputs"]), max_length=max_print_outputs_length)


def _parse_expression(code: str) -> ast.Module:
    try:
        return ast.parse(code)
    except SyntaxError as exc:
        raise InterpreterError(
            f"Code parsing failed on line {exc.lineno} due to: {type(exc).__name__}: {exc}\n"
            f"{exc.text}"
            f"{' ' * (exc.offset or 0)}^"
        ) from exc


async def evaluate_python_code_async(
    code: str,
    static_tools: dict[str, Callable] | None = None,
    custom_tools: dict[str, Callable] | None = None,
    state: dict[str, Any] | None = None,
    authorized_imports: list[str] = BASE_BUILTIN_MODULES,
    max_print_outputs_length: int = DEFAULT_MAX_LEN_OUTPUT,
    timeout_seconds: int | None = MAX_EXECUTION_TIME_SECONDS,
):
    code = fix_final_answer_code(code)
    expression = _parse_expression(code)

    if state is None:
        state = {}

    static_tools = static_tools.copy() if static_tools is not None else {}
    custom_tools = custom_tools if custom_tools is not None else {}
    state["_print_outputs"] = PrintContainer()
    state["_operations_count"] = {"counter": 0}

    if "final_answer" in static_tools:
        static_tools["final_answer"] = stmt_handlers.wrap_final_answer_tool(static_tools["final_answer"])

    context = EvaluationContext(
        state=state,
        static_tools=static_tools,
        custom_tools=custom_tools,
        authorized_imports=authorized_imports,
    )

    async def _execute_code():
        result = None
        current_node: ast.AST | None = None
        try:
            for current_node in expression.body:
                result = await evaluate_ast(current_node, context)
            _finalize_print_outputs(state, max_print_outputs_length)
            return result, False
        except FinalAnswerException as exc:
            _finalize_print_outputs(state, max_print_outputs_length)
            return exc.value, True
        except Exception as exc:
            _finalize_print_outputs(state, max_print_outputs_length)
            current_line = ast.get_source_segment(code, current_node) if current_node is not None else "<unknown>"
            raise InterpreterError(
                f"Code execution failed at line '{current_line}' due to: {type(exc).__name__}: {exc}"
            ) from exc

    try:
        if timeout_seconds is not None:
            return await asyncio.wait_for(_execute_code(), timeout=timeout_seconds)
        return await _execute_code()
    except asyncio.TimeoutError as exc:
        raise ExecutionTimeoutError(
            f"Code execution exceeded the maximum execution time of {timeout_seconds} seconds"
        ) from exc


def evaluate_python_code(
    code: str,
    static_tools: dict[str, Callable] | None = None,
    custom_tools: dict[str, Callable] | None = None,
    state: dict[str, Any] | None = None,
    authorized_imports: list[str] = BASE_BUILTIN_MODULES,
    max_print_outputs_length: int = DEFAULT_MAX_LEN_OUTPUT,
    timeout_seconds: int | None = MAX_EXECUTION_TIME_SECONDS,
):
    return run_coroutine_sync(
        evaluate_python_code_async(
            code,
            static_tools=static_tools,
            custom_tools=custom_tools,
            state=state,
            authorized_imports=authorized_imports,
            max_print_outputs_length=max_print_outputs_length,
            timeout_seconds=timeout_seconds,
        ),
        timeout_seconds=timeout_seconds,
    )
