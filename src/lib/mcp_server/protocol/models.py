from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, ValidationError

from lib.mcp_server.protocol.constants import JSONRPC_VERSION
from lib.mcp_server.protocol.errors import INVALID_REQUEST, MCPProtocolError

RequestId = str | StrictInt | StrictFloat | None


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class _PublicModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ParamsModel(_StrictModel):
    meta: dict[str, Any] | None = Field(default=None, alias="_meta")


class PaginatedParams(ParamsModel):
    cursor: str | None = None


class JSONRPCRequestEnvelope(_StrictModel):
    jsonrpc: Literal["2.0"]
    id: RequestId
    method: str
    params: dict[str, Any] | None = None


class JSONRPCNotificationEnvelope(_StrictModel):
    jsonrpc: Literal["2.0"]
    method: str
    params: dict[str, Any] | None = None


class ErrorData(_PublicModel):
    code: int
    message: str
    data: Any | None = None


class JSONRPCSuccessResponse(_PublicModel):
    jsonrpc: Literal["2.0"] = JSONRPC_VERSION
    id: RequestId
    result: dict[str, Any]


class JSONRPCErrorResponse(_PublicModel):
    jsonrpc: Literal["2.0"] = JSONRPC_VERSION
    id: RequestId
    error: ErrorData


class ToolsCapability(_PublicModel):
    listChanged: bool = False


class ResourcesCapability(_PublicModel):
    subscribe: bool = False
    listChanged: bool = False


class PromptsCapability(_PublicModel):
    listChanged: bool = False


class ServerCapabilities(_PublicModel):
    tools: ToolsCapability | None = None
    resources: ResourcesCapability | None = None
    prompts: PromptsCapability | None = None


class ServerInfo(_PublicModel):
    name: str
    title: str | None = None
    version: str


class InitializeParams(ParamsModel):
    protocolVersion: str
    capabilities: dict[str, Any] = Field(default_factory=dict)
    clientInfo: dict[str, Any] = Field(default_factory=dict)


class InitializeResult(_PublicModel):
    protocolVersion: str
    capabilities: ServerCapabilities
    serverInfo: ServerInfo
    instructions: str = ""


class EmptyResult(_PublicModel):
    pass


class Tool(_PublicModel):
    name: str
    title: str | None = None
    description: str | None = None
    inputSchema: dict[str, Any]
    outputSchema: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None


class ListToolsResult(_PublicModel):
    tools: list[Tool]


class CallToolRequestParams(ParamsModel):
    name: str
    arguments: dict[str, Any] | None = None


class CallToolResult(_PublicModel):
    content: list[dict[str, Any]]
    structuredContent: dict[str, Any] | None = None
    isError: bool = False


class Resource(_PublicModel):
    name: str
    title: str | None = None
    uri: str
    description: str | None = None
    mimeType: str | None = None
    annotations: dict[str, Any] | None = None


class ResourceTemplate(_PublicModel):
    name: str
    title: str | None = None
    uriTemplate: str
    description: str | None = None
    mimeType: str | None = None
    annotations: dict[str, Any] | None = None


class ListResourcesResult(_PublicModel):
    resources: list[Resource]


class ListResourceTemplatesResult(_PublicModel):
    resourceTemplates: list[ResourceTemplate]


class ReadResourceRequestParams(ParamsModel):
    uri: str


class ReadResourceResult(_PublicModel):
    contents: list[dict[str, Any]]


class PromptArgument(_PublicModel):
    name: str
    description: str | None = None
    required: bool | None = None


class Prompt(_PublicModel):
    name: str
    title: str | None = None
    description: str | None = None
    arguments: list[PromptArgument] | None = None


class ListPromptsResult(_PublicModel):
    prompts: list[Prompt]


class GetPromptRequestParams(ParamsModel):
    name: str
    arguments: dict[str, str] | None = None


class GetPromptResult(_PublicModel):
    description: str | None = None
    messages: list[dict[str, Any]]


@dataclass(frozen=True)
class JSONRPCRequest:
    method: str
    request_id: RequestId
    has_id: bool
    params: dict[str, Any] | None = None

    @property
    def is_notification(self) -> bool:
        return not self.has_id


def parse_request(payload: Any) -> JSONRPCRequest:
    if not isinstance(payload, dict):
        raise MCPProtocolError(INVALID_REQUEST, "Request must be a JSON object.")

    try:
        if "id" in payload:
            request = JSONRPCRequestEnvelope.model_validate(payload)
            return JSONRPCRequest(
                method=request.method,
                request_id=request.id,
                has_id=True,
                params=request.params,
            )
        notification = JSONRPCNotificationEnvelope.model_validate(payload)
        return JSONRPCRequest(
            method=notification.method,
            request_id=None,
            has_id=False,
            params=notification.params,
        )
    except ValidationError as exc:
        raise MCPProtocolError(INVALID_REQUEST, _format_validation_error(exc)) from exc


def parse_params(model_type: type[BaseModel], raw_params: dict[str, Any] | None, *, name: str = "params") -> Any:
    payload = raw_params or {}
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise MCPProtocolError(-32602, _format_validation_error(exc, prefix=name)) from exc


def make_result_response(request_id: RequestId, result: BaseModel | dict[str, Any]) -> dict[str, Any]:
    payload = result.model_dump(exclude_none=True, by_alias=True) if isinstance(result, BaseModel) else result
    return JSONRPCSuccessResponse(id=request_id, result=payload).model_dump(exclude_none=True, by_alias=True)


def make_error_response(request_id: RequestId, error: MCPProtocolError) -> dict[str, Any]:
    return JSONRPCErrorResponse(
        id=request_id,
        error=ErrorData(code=error.code, message=error.message, data=error.data),
    ).model_dump(exclude_none=True, by_alias=True)


def _format_validation_error(exc: ValidationError, *, prefix: str | None = None) -> str:
    error = exc.errors(include_url=False)[0]
    location = ".".join(str(item) for item in error.get("loc", ()) if item != "__root__")
    if prefix and location:
        location = f"{prefix}.{location}"
    elif prefix:
        location = prefix
    message = _humanize_validation_error(error)
    if not location:
        return message
    if message.startswith(("must ", "is ")):
        return f"{location} {message}"
    return f"{location}: {message}"


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
    return error.get("msg", "Invalid request.")
