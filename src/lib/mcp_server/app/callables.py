from __future__ import annotations

import asyncio
import functools
import inspect
import json
from dataclasses import dataclass
from typing import Any, get_args, get_origin, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from lib.mcp_server.app.context import RequestContext
from lib.mcp_server.protocol.errors import INVALID_PARAMS, MCPProtocolError


class _ArgumentModelBase(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
    )

    def model_dump_one_level(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for field_name in self.__class__.model_fields:
            values[field_name] = getattr(self, field_name)
        return values


@dataclass(frozen=True)
class PromptArgumentDefinition:
    name: str
    required: bool
    description: str | None = None


@dataclass(frozen=True)
class CallableMetadata:
    handler: Any
    name: str
    title: str | None
    description: str
    signature: inspect.Signature
    type_hints: dict[str, Any]
    arg_model: type[_ArgumentModelBase]
    context_parameter: str | None
    is_async: bool
    input_schema: dict[str, Any]

    @classmethod
    def from_function(
        cls,
        handler: Any,
        *,
        name: str,
        title: str | None = None,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
    ) -> "CallableMetadata":
        signature = inspect.signature(handler)
        _validate_signature(signature)
        type_hints = get_type_hints(handler, include_extras=True)
        context_parameter = find_context_parameter(type_hints)
        arg_model = _build_arg_model(handler, signature, type_hints, context_parameter)
        schema = input_schema or arg_model.model_json_schema(by_alias=True)
        return cls(
            handler=handler,
            name=name,
            title=title,
            description=description or inspect.getdoc(handler) or "",
            signature=signature,
            type_hints=type_hints,
            arg_model=arg_model,
            context_parameter=context_parameter,
            is_async=_is_async_callable(handler),
            input_schema=schema,
        )

    async def invoke(self, arguments: dict[str, Any] | None, context: RequestContext | None = None) -> Any:
        raw_arguments = arguments or {}
        prepared_arguments = self._pre_parse_json(raw_arguments)
        try:
            validated = self.arg_model.model_validate(prepared_arguments)
        except ValidationError as exc:
            raise MCPProtocolError(INVALID_PARAMS, _format_validation_error(exc, prefix="params.arguments")) from exc

        kwargs = validated.model_dump_one_level()
        if self.context_parameter is not None:
            kwargs[self.context_parameter] = context

        try:
            if self.is_async:
                return await self.handler(**kwargs)
            return await asyncio.to_thread(functools.partial(self.handler, **kwargs))
        except MCPProtocolError:
            raise

    def prompt_arguments(self) -> list[PromptArgumentDefinition]:
        result: list[PromptArgumentDefinition] = []
        for parameter in self.signature.parameters.values():
            if parameter.name == self.context_parameter:
                continue
            field_info = self.arg_model.model_fields.get(parameter.name)
            result.append(
                PromptArgumentDefinition(
                    name=parameter.name,
                    required=parameter.default is inspect.Signature.empty,
                    description=field_info.description if field_info is not None else None,
                )
            )
        return result

    def _pre_parse_json(self, arguments: dict[str, Any]) -> dict[str, Any]:
        parsed = dict(arguments)
        for key, value in arguments.items():
            field_info = self.arg_model.model_fields.get(key)
            if field_info is None:
                continue
            if isinstance(value, str) and field_info.annotation is not str:
                try:
                    candidate = json.loads(value)
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, (str, int, float, bool)) or candidate is None:
                    continue
                parsed[key] = candidate
        return parsed


def find_context_parameter(type_hints: dict[str, Any]) -> str | None:
    for parameter_name, annotation in type_hints.items():
        if _matches_request_context(annotation):
            return parameter_name
    return None


def _build_arg_model(
    handler: Any,
    signature: inspect.Signature,
    type_hints: dict[str, Any],
    context_parameter: str | None,
) -> type[_ArgumentModelBase]:
    field_definitions: dict[str, tuple[Any, Any]] = {}
    for parameter in signature.parameters.values():
        if parameter.name == context_parameter:
            continue
        annotation = type_hints.get(parameter.name, Any)
        default = parameter.default if parameter.default is not inspect.Signature.empty else ...
        field_definitions[parameter.name] = (annotation, default)
    model_name = f"{handler.__name__.title()}Arguments"
    return create_model(model_name, __base__=_ArgumentModelBase, **field_definitions)


def _matches_request_context(annotation: Any) -> bool:
    if annotation is RequestContext:
        return True

    origin = get_origin(annotation)
    if origin is None:
        return False

    return any(_matches_request_context(arg) for arg in get_args(annotation))


def _validate_signature(signature: inspect.Signature) -> None:
    for parameter in signature.parameters.values():
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.VAR_POSITIONAL):
            raise ValueError("MCP handlers do not support positional-only or *args parameters.")
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            raise ValueError("MCP handlers do not support **kwargs parameters.")


def _is_async_callable(handler: Any) -> bool:
    return inspect.iscoroutinefunction(handler) or (
        callable(handler) and inspect.iscoroutinefunction(getattr(handler, "__call__", None))
    )


def _format_validation_error(exc: ValidationError, *, prefix: str | None = None) -> str:
    error = exc.errors(include_url=False)[0]
    path = ".".join(str(item) for item in error.get("loc", ()) if item != "__root__")
    if prefix and path:
        path = f"{prefix}.{path}"
    elif prefix:
        path = prefix
    message = _humanize_validation_error(error)
    if not path:
        return message
    if message.startswith(("must ", "is ")):
        return f"{path} {message}"
    return f"{path}: {message}"


def _humanize_validation_error(error: dict[str, Any]) -> str:
    error_type = error.get("type")
    if error_type == "dict_type":
        return "must be an object."
    if error_type == "string_type":
        return "must be a string."
    if error_type == "int_type":
        return "must be an integer."
    if error_type == "float_type":
        return "must be a number."
    if error_type == "bool_type":
        return "must be a boolean."
    if error_type == "missing":
        return "is required."
    if error_type == "extra_forbidden":
        return "contains unsupported fields."
    return error.get("msg", "Invalid value.")
