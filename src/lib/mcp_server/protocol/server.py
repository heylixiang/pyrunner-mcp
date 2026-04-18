from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from lib.mcp_server.app.context import RequestContext
from lib.mcp_server.app.registries import PromptRegistry, ResourceRegistry, ToolRegistry
from lib.mcp_server.protocol.constants import DEFAULT_PROTOCOL_VERSION, SUPPORTED_PROTOCOL_VERSIONS
from lib.mcp_server.protocol.errors import INTERNAL_ERROR, INVALID_PARAMS, MCPNotInitializedError, MCPProtocolError
from lib.mcp_server.protocol.models import (
    CallToolRequestParams,
    EmptyResult,
    GetPromptRequestParams,
    GetPromptResult,
    InitializeParams,
    InitializeResult,
    JSONRPCRequest,
    ListPromptsResult,
    ListResourcesResult,
    ListResourceTemplatesResult,
    ListToolsResult,
    PaginatedParams,
    ReadResourceRequestParams,
    ReadResourceResult,
    ServerCapabilities,
    ServerInfo,
    ToolsCapability,
    ResourcesCapability,
    PromptsCapability,
    make_error_response,
    make_result_response,
    parse_params,
    parse_request,
)


@dataclass(frozen=True)
class MCPServerConfig:
    server_name: str
    server_title: str
    server_version: str
    instructions: str = ""
    supported_protocol_versions: tuple[str, ...] | None = field(default_factory=lambda: SUPPORTED_PROTOCOL_VERSIONS)
    enable_tools: bool = True
    enable_resources: bool = True
    enable_prompts: bool = True


class MCPServerSession:
    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        resource_registry: ResourceRegistry,
        prompt_registry: PromptRegistry,
        config: MCPServerConfig,
    ):
        self._tool_registry = tool_registry
        self._resource_registry = resource_registry
        self._prompt_registry = prompt_registry
        self._config = config
        self._initialized = False
        self._protocol_version: str | None = None
        self._client_info: dict[str, Any] | None = None
        self._client_capabilities: dict[str, Any] | None = None

    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def protocol_version(self) -> str | None:
        return self._protocol_version

    @property
    def client_info(self) -> dict[str, Any] | None:
        return self._client_info

    async def handle_message(self, payload: Any) -> dict[str, Any] | list[dict[str, Any]] | None:
        if isinstance(payload, list):
            if not payload:
                return make_error_response(None, MCPProtocolError(-32600, "JSON-RPC batch requests must not be empty."))
            responses: list[dict[str, Any]] = []
            for item in payload:
                response = await self._handle_single_payload(item)
                if response is not None:
                    responses.append(response)
            return responses or None
        return await self._handle_single_payload(payload)

    async def _handle_single_payload(self, payload: Any) -> dict[str, Any] | None:
        try:
            request = parse_request(payload)
        except MCPProtocolError as exc:
            request_id = payload.get("id") if isinstance(payload, dict) else None
            return make_error_response(request_id, exc)

        label = _request_label(request)
        start = time.monotonic()
        try:
            response = await self._dispatch_request(request)
            elapsed_ms = (time.monotonic() - start) * 1000
            if request.is_notification:
                _log.debug("%s", label)
            else:
                _log.info("%s %.0fms", label, elapsed_ms)
            return response
        except MCPProtocolError as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            _log.error("%s error(%d) %.0fms", label, exc.code, elapsed_ms, exc_info=exc)
            if request.is_notification:
                return None
            return make_error_response(request.request_id, exc)
        except Exception as exc:  # pragma: no cover - defensive guard
            elapsed_ms = (time.monotonic() - start) * 1000
            _log.error("%s error %.0fms", label, elapsed_ms, exc_info=exc)
            if request.is_notification:
                return None
            return make_error_response(
                request.request_id,
                MCPProtocolError(INTERNAL_ERROR, str(exc) or type(exc).__name__),
            )

    async def _dispatch_request(self, request: JSONRPCRequest) -> dict[str, Any] | None:
        if request.method == "initialize":
            return self._handle_initialize(request)

        if request.method == "ping":
            if request.is_notification:
                return None
            return make_result_response(request.request_id, EmptyResult())

        if request.method.startswith("notifications/"):
            return self._handle_notification(request)

        if not self._initialized:
            raise MCPNotInitializedError()

        if request.method == "tools/list":
            self._ensure_capability(self._config.enable_tools, request.method)
            return self._handle_tools_list(request)
        if request.method == "tools/call":
            self._ensure_capability(self._config.enable_tools, request.method)
            return await self._handle_tools_call(request)
        if request.method == "resources/list":
            self._ensure_capability(self._config.enable_resources, request.method)
            return self._handle_resources_list(request)
        if request.method == "resources/templates/list":
            self._ensure_capability(self._config.enable_resources, request.method)
            return self._handle_resource_templates_list(request)
        if request.method == "resources/read":
            self._ensure_capability(self._config.enable_resources, request.method)
            return await self._handle_resources_read(request)
        if request.method == "prompts/list":
            self._ensure_capability(self._config.enable_prompts, request.method)
            return self._handle_prompts_list(request)
        if request.method == "prompts/get":
            self._ensure_capability(self._config.enable_prompts, request.method)
            return await self._handle_prompts_get(request)

        raise MCPProtocolError(-32601, f"Method not found: {request.method}")

    def _handle_initialize(self, request: JSONRPCRequest) -> dict[str, Any]:
        if self._initialized:
            raise MCPProtocolError(-32600, "Server is already initialized.")
        if request.is_notification:
            raise MCPProtocolError(-32600, "initialize must be sent as a request.")

        params = parse_params(InitializeParams, request.params)
        supported_versions = tuple(self._config.supported_protocol_versions or SUPPORTED_PROTOCOL_VERSIONS)
        if params.protocolVersion not in supported_versions:
            raise MCPProtocolError(
                INVALID_PARAMS,
                f"Unsupported protocolVersion: {params.protocolVersion}",
                data={"supported": list(supported_versions)},
            )

        self._protocol_version = params.protocolVersion
        self._client_info = params.clientInfo
        self._client_capabilities = params.capabilities
        self._initialized = True

        result = InitializeResult(
            protocolVersion=self._protocol_version or DEFAULT_PROTOCOL_VERSION,
            capabilities=self._server_capabilities(),
            serverInfo=ServerInfo(
                name=self._config.server_name,
                title=self._config.server_title,
                version=self._config.server_version,
            ),
            instructions=self._config.instructions,
        )
        return make_result_response(request.request_id, result)

    def _handle_notification(self, request: JSONRPCRequest) -> None:
        if request.method in {"notifications/initialized", "notifications/cancelled"}:
            return None
        return None

    def _handle_tools_list(self, request: JSONRPCRequest) -> dict[str, Any] | None:
        if request.is_notification:
            return None
        parse_params(PaginatedParams, request.params)
        return make_result_response(request.request_id, ListToolsResult(tools=self._tool_registry.list_tools()))

    async def _handle_tools_call(self, request: JSONRPCRequest) -> dict[str, Any]:
        if request.is_notification:
            raise MCPProtocolError(-32600, "tools/call must be sent as a request.")
        params = parse_params(CallToolRequestParams, request.params)
        result = await self._tool_registry.call_tool(params.name, params.arguments, self._build_context(request))
        return make_result_response(request.request_id, result)

    def _handle_resources_list(self, request: JSONRPCRequest) -> dict[str, Any] | None:
        if request.is_notification:
            return None
        parse_params(PaginatedParams, request.params)
        return make_result_response(
            request.request_id,
            ListResourcesResult(resources=self._resource_registry.list_resources()),
        )

    def _handle_resource_templates_list(self, request: JSONRPCRequest) -> dict[str, Any] | None:
        if request.is_notification:
            return None
        parse_params(PaginatedParams, request.params)
        return make_result_response(
            request.request_id,
            ListResourceTemplatesResult(resourceTemplates=self._resource_registry.list_resource_templates()),
        )

    async def _handle_resources_read(self, request: JSONRPCRequest) -> dict[str, Any]:
        if request.is_notification:
            raise MCPProtocolError(-32600, "resources/read must be sent as a request.")
        params = parse_params(ReadResourceRequestParams, request.params)
        result = await self._resource_registry.read_resource(params.uri, self._build_context(request))
        return make_result_response(request.request_id, ReadResourceResult(**result))

    def _handle_prompts_list(self, request: JSONRPCRequest) -> dict[str, Any] | None:
        if request.is_notification:
            return None
        parse_params(PaginatedParams, request.params)
        return make_result_response(request.request_id, ListPromptsResult(prompts=self._prompt_registry.list_prompts()))

    async def _handle_prompts_get(self, request: JSONRPCRequest) -> dict[str, Any]:
        if request.is_notification:
            raise MCPProtocolError(-32600, "prompts/get must be sent as a request.")
        params = parse_params(GetPromptRequestParams, request.params)
        result = await self._prompt_registry.get_prompt(params.name, params.arguments, self._build_context(request))
        return make_result_response(request.request_id, GetPromptResult(**result))

    def _build_context(self, request: JSONRPCRequest) -> RequestContext:
        return RequestContext(
            method=request.method,
            params=request.params,
            request_id=request.request_id,
            session=self,
            server_name=self._config.server_name,
            client_info=self._client_info,
            protocol_version=self._protocol_version,
        )

    def _server_capabilities(self) -> ServerCapabilities:
        return ServerCapabilities(
            tools=ToolsCapability() if self._config.enable_tools else None,
            resources=ResourcesCapability() if self._config.enable_resources else None,
            prompts=PromptsCapability() if self._config.enable_prompts else None,
        )

    def _ensure_capability(self, enabled: bool, method: str) -> None:
        if not enabled:
            raise MCPProtocolError(-32601, f"Method not found: {method}")


_log = logging.getLogger("mcp.server")


def _request_label(request: JSONRPCRequest) -> str:
    method = request.method
    params = request.params if isinstance(request.params, dict) else {}
    if method == "tools/call":
        return f"{method} [{params.get('name', '?')}]"
    if method == "resources/read":
        return f"{method} [{params.get('uri', '?')}]"
    if method == "prompts/get":
        return f"{method} [{params.get('name', '?')}]"
    return method


class MCPServer:
    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        resource_registry: ResourceRegistry,
        prompt_registry: PromptRegistry,
        config: MCPServerConfig,
        cleanup_callbacks: list[Callable[[], Any]] | None = None,
    ):
        self._tool_registry = tool_registry
        self._resource_registry = resource_registry
        self._prompt_registry = prompt_registry
        self._config = config
        self._cleanup_callbacks = list(cleanup_callbacks or [])
        self._default_session: MCPServerSession | None = None
        self._closed = False

    def create_session(self) -> MCPServerSession:
        return MCPServerSession(
            tool_registry=self._tool_registry,
            resource_registry=self._resource_registry,
            prompt_registry=self._prompt_registry,
            config=self._config,
        )

    async def handle_message(self, payload: Any) -> dict[str, Any] | list[dict[str, Any]] | None:
        if self._default_session is None:
            self._default_session = self.create_session()
        return await self._default_session.handle_message(payload)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for callback in reversed(self._cleanup_callbacks):
            callback()

    def __enter__(self) -> "MCPServer":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
