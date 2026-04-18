from __future__ import annotations

from collections.abc import Callable
from typing import Any

from lib.mcp_server.app.callables import CallableMetadata
from lib.mcp_server.app.context import RequestContext
from lib.mcp_server.app.entities import PromptDefinition, ResourceDefinition, ToolDefinition
from lib.mcp_server.app.results import ToolExecutionError, normalize_tool_result
from lib.mcp_server.protocol.errors import INVALID_PARAMS, MCPProtocolError
from lib.mcp_server.protocol.models import Prompt, Resource, ResourceTemplate, Tool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def add_tool(
        self,
        handler: Callable[..., Any],
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
        annotations: dict[str, Any] | None = None,
    ) -> ToolDefinition:
        tool_name = name or handler.__name__
        if tool_name in self._tools:
            raise ValueError(f"Tool already registered: {tool_name}")
        tool = ToolDefinition(
            metadata=CallableMetadata.from_function(
                handler,
                name=tool_name,
                title=title,
                description=description,
                input_schema=input_schema,
            ),
            annotations=annotations,
        )
        self._tools[tool_name] = tool
        return tool

    def list_tools(self) -> list[Tool]:
        return [tool.to_model() for tool in self._tools.values()]

    async def call_tool(self, name: str, arguments: dict[str, Any] | None, context: RequestContext | None) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            raise MCPProtocolError(INVALID_PARAMS, f"Unknown tool: {name}")
        try:
            value = await tool.metadata.invoke(arguments, context)
        except ToolExecutionError as exc:
            return exc.to_tool_result().to_payload()
        return normalize_tool_result(value)


class ResourceRegistry:
    def __init__(self) -> None:
        self._resources_by_name: dict[str, ResourceDefinition] = {}

    def add_resource(
        self,
        handler: Callable[..., Any],
        *,
        uri_template: str,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
        annotations: dict[str, Any] | None = None,
    ) -> ResourceDefinition:
        resource_name = name or handler.__name__
        if resource_name in self._resources_by_name:
            raise ValueError(f"Resource already registered: {resource_name}")
        definition = ResourceDefinition(
            metadata=CallableMetadata.from_function(
                handler,
                name=resource_name,
                title=title,
                description=description,
            ),
            uri_template=uri_template,
            mime_type=mime_type,
            annotations=annotations,
        )
        self._resources_by_name[resource_name] = definition
        return definition

    def list_resources(self) -> list[Resource]:
        return [resource.to_resource_model() for resource in self._resources_by_name.values() if not resource.is_template]

    def list_resource_templates(self) -> list[ResourceTemplate]:
        return [resource.to_template_model() for resource in self._resources_by_name.values() if resource.is_template]

    async def read_resource(self, uri: str, context: RequestContext | None) -> dict[str, Any]:
        for resource in self._resources_by_name.values():
            if resource.match(uri) is None:
                continue
            return await resource.read(uri, context)
        raise MCPProtocolError(INVALID_PARAMS, f"Unknown resource: {uri}")


class PromptRegistry:
    def __init__(self) -> None:
        self._prompts: dict[str, PromptDefinition] = {}

    def add_prompt(
        self,
        handler: Callable[..., Any],
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
    ) -> PromptDefinition:
        prompt_name = name or handler.__name__
        if prompt_name in self._prompts:
            raise ValueError(f"Prompt already registered: {prompt_name}")
        definition = PromptDefinition(
            metadata=CallableMetadata.from_function(
                handler,
                name=prompt_name,
                title=title,
                description=description,
            )
        )
        self._prompts[prompt_name] = definition
        return definition

    def list_prompts(self) -> list[Prompt]:
        return [prompt.to_model() for prompt in self._prompts.values()]

    async def get_prompt(self, name: str, arguments: dict[str, Any] | None, context: RequestContext | None) -> dict[str, Any]:
        prompt = self._prompts.get(name)
        if prompt is None:
            raise MCPProtocolError(INVALID_PARAMS, f"Unknown prompt: {name}")
        return await prompt.get_prompt(arguments, context)
