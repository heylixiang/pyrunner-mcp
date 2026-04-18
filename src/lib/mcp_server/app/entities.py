from __future__ import annotations

import re
from dataclasses import dataclass
from string import Formatter
from typing import Any

from lib.mcp_server.app.callables import CallableMetadata
from lib.mcp_server.app.context import RequestContext
from lib.mcp_server.app.results import normalize_prompt_result, normalize_resource_result
from lib.mcp_server.protocol.models import Prompt, PromptArgument, Resource, ResourceTemplate, Tool


@dataclass(frozen=True)
class ToolDefinition:
    metadata: CallableMetadata
    annotations: dict[str, Any] | None = None

    def to_model(self) -> Tool:
        return Tool(
            name=self.metadata.name,
            title=self.metadata.title or _title_from_name(self.metadata.name),
            description=self.metadata.description,
            inputSchema=self.metadata.input_schema,
            annotations=self.annotations,
        )


@dataclass(frozen=True)
class ResourceDefinition:
    metadata: CallableMetadata
    uri_template: str
    mime_type: str | None = None
    annotations: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "_pattern", _compile_uri_template(self.uri_template))
        object.__setattr__(self, "_field_names", tuple(_extract_template_fields(self.uri_template)))

    @property
    def is_template(self) -> bool:
        return bool(self._field_names)

    def to_resource_model(self) -> Resource:
        return Resource(
            name=self.metadata.name,
            title=self.metadata.title or _title_from_name(self.metadata.name),
            uri=self.uri_template,
            description=self.metadata.description or None,
            mimeType=self.mime_type,
            annotations=self.annotations,
        )

    def to_template_model(self) -> ResourceTemplate:
        return ResourceTemplate(
            name=self.metadata.name,
            title=self.metadata.title or _title_from_name(self.metadata.name),
            uriTemplate=self.uri_template,
            description=self.metadata.description or None,
            mimeType=self.mime_type,
            annotations=self.annotations,
        )

    def match(self, uri: str) -> dict[str, str] | None:
        match = self._pattern.fullmatch(uri)
        if match is None:
            return None
        return {key: value for key, value in match.groupdict().items() if value is not None}

    async def read(self, uri: str, context: RequestContext | None) -> dict[str, Any]:
        arguments = self.match(uri)
        if arguments is None:
            raise ValueError(f"Unknown resource: {uri}")
        value = await self.metadata.invoke(arguments, context)
        return normalize_resource_result(uri, self.mime_type, value)


@dataclass(frozen=True)
class PromptDefinition:
    metadata: CallableMetadata

    def to_model(self) -> Prompt:
        arguments = [
            PromptArgument(name=argument.name, description=argument.description, required=argument.required)
            for argument in self.metadata.prompt_arguments()
        ]
        return Prompt(
            name=self.metadata.name,
            title=self.metadata.title or _title_from_name(self.metadata.name),
            description=self.metadata.description or None,
            arguments=arguments or None,
        )

    async def get_prompt(self, arguments: dict[str, Any] | None, context: RequestContext | None) -> dict[str, Any]:
        value = await self.metadata.invoke(arguments, context)
        return normalize_prompt_result(value)


def _compile_uri_template(uri_template: str) -> re.Pattern[str]:
    pattern = "^"
    formatter = Formatter()
    for literal_text, field_name, _, _ in formatter.parse(uri_template):
        pattern += re.escape(literal_text)
        if field_name is not None:
            pattern += rf"(?P<{field_name}>[^/?#]+)"
    pattern += "$"
    return re.compile(pattern)


def _extract_template_fields(uri_template: str) -> list[str]:
    formatter = Formatter()
    fields: list[str] = []
    for _, field_name, _, _ in formatter.parse(uri_template):
        if field_name is not None:
            fields.append(field_name)
    return fields


def _title_from_name(name: str) -> str:
    return name.replace("_", " ").strip().title()
