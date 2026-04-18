from __future__ import annotations

import base64
import json
from dataclasses import asdict, is_dataclass
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from lib.sandbox_runner.protocol import serialize_value


class ToolResult(BaseModel):
    content: list[dict[str, Any]]
    structured_content: dict[str, Any] | None = Field(default=None, alias="structuredContent")
    is_error: bool = Field(default=False, alias="isError")

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


class ToolExecutionError(Exception):
    def __init__(
        self,
        message: str,
        *,
        structured_content: dict[str, Any] | None = None,
        content: Sequence[dict[str, Any]] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.structured_content = structured_content
        self.content = list(content or [])

    def __str__(self) -> str:
        return self.message

    def to_tool_result(self) -> ToolResult:
        structured_content = self.structured_content or {
            "ok": False,
            "error": {
                "message": self.message,
                "type": type(self).__name__,
            },
        }
        content = self.content or [_text_content(json.dumps(structured_content, ensure_ascii=False, indent=2, sort_keys=True))]
        return ToolResult(content=content, structuredContent=structured_content, isError=True)


def normalize_tool_result(value: Any) -> dict[str, Any]:
    if isinstance(value, ToolResult):
        return value.to_payload()

    serialized = serialize_for_wire(value)
    if isinstance(serialized, dict):
        structured_content = serialized
        text = json.dumps(serialized, ensure_ascii=False, indent=2, sort_keys=True)
    else:
        structured_content = {"result": serialized}
        if isinstance(serialized, str):
            text = serialized
        else:
            text = json.dumps(serialized, ensure_ascii=False)

    return ToolResult(
        content=[_text_content(text)],
        structuredContent=structured_content,
        isError=False,
    ).to_payload()


def normalize_resource_result(uri: str, mime_type: str | None, value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping) and "contents" in value:
        contents = [_normalize_resource_content(uri, mime_type, item) for item in value["contents"]]
    elif isinstance(value, list):
        contents = [_normalize_resource_content(uri, mime_type, item) for item in value]
    else:
        contents = [_normalize_resource_content(uri, mime_type, value)]
    return {"contents": contents}


def normalize_prompt_result(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping) and "messages" in value:
        description = value.get("description")
        messages = [_normalize_prompt_message(item) for item in value["messages"]]
        payload: dict[str, Any] = {"messages": messages}
        if description is not None:
            payload["description"] = description
        return payload

    if isinstance(value, list):
        return {"messages": [_normalize_prompt_message(item) for item in value]}

    if isinstance(value, str):
        return {"messages": [{"role": "user", "content": _text_content(value)}]}

    serialized = serialize_for_wire(value)
    return {
        "messages": [
            {
                "role": "user",
                "content": _text_content(json.dumps(serialized, ensure_ascii=False, indent=2, sort_keys=True)),
            }
        ]
    }


def serialize_for_wire(value: Any) -> Any:
    return serialize_value(_to_json_ready(value))


def _to_json_ready(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return {
            key: _to_json_ready(item)
            for key, item in value.model_dump(mode="python", by_alias=True, exclude_none=True).items()
        }

    if is_dataclass(value) and not isinstance(value, type):
        return {key: _to_json_ready(item) for key, item in asdict(value).items()}

    if isinstance(value, Mapping):
        return {key: _to_json_ready(item) for key, item in value.items()}

    if isinstance(value, list):
        return [_to_json_ready(item) for item in value]

    if isinstance(value, tuple):
        return tuple(_to_json_ready(item) for item in value)

    if isinstance(value, set):
        return {_to_json_ready(item) for item in value}

    return value


def _normalize_resource_content(uri: str, mime_type: str | None, value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping) and ("text" in value or "blob" in value):
        payload = {key: serialize_for_wire(item) for key, item in value.items()}
        payload.setdefault("uri", uri)
        if mime_type is not None:
            payload.setdefault("mimeType", mime_type)
        return payload

    if isinstance(value, bytes):
        payload: dict[str, Any] = {
            "uri": uri,
            "blob": base64.b64encode(value).decode("ascii"),
        }
        payload["mimeType"] = mime_type or "application/octet-stream"
        return payload

    if isinstance(value, str):
        payload = {
            "uri": uri,
            "text": value,
        }
        if mime_type is not None:
            payload["mimeType"] = mime_type
        return payload

    serialized = serialize_for_wire(value)
    payload = {
        "uri": uri,
        "text": json.dumps(serialized, ensure_ascii=False, indent=2, sort_keys=True),
    }
    if mime_type is not None:
        payload["mimeType"] = mime_type
    return payload


def _normalize_prompt_message(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        role = value.get("role")
        content = value.get("content")
        if isinstance(role, str) and isinstance(content, Mapping) and "type" in content:
            return {
                "role": role,
                "content": {key: serialize_for_wire(item) for key, item in content.items()},
            }
        if "type" in value:
            return {"role": "user", "content": {key: serialize_for_wire(item) for key, item in value.items()}}
        if "text" in value:
            return {"role": "user", "content": _text_content(str(value["text"]))}

    if isinstance(value, str):
        return {"role": "user", "content": _text_content(value)}

    serialized = serialize_for_wire(value)
    return {"role": "user", "content": _text_content(json.dumps(serialized, ensure_ascii=False, indent=2, sort_keys=True))}


def _text_content(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}
