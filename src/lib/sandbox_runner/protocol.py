from __future__ import annotations

import json
import traceback
from dataclasses import asdict, dataclass, field
from typing import Any


WIRE_TYPE_KEY = "__sandbox_type__"


def serialize_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if isinstance(value, list):
        return [serialize_value(item) for item in value]

    if isinstance(value, tuple):
        return {WIRE_TYPE_KEY: "tuple", "items": [serialize_value(item) for item in value]}

    if isinstance(value, set):
        return {WIRE_TYPE_KEY: "set", "items": [serialize_value(item) for item in value]}

    if isinstance(value, dict):
        if all(isinstance(key, str) and key != WIRE_TYPE_KEY for key in value):
            return {key: serialize_value(item) for key, item in value.items()}
        return {
            WIRE_TYPE_KEY: "dict",
            "items": [[serialize_value(key), serialize_value(item)] for key, item in value.items()],
        }

    return {
        WIRE_TYPE_KEY: "repr",
        "type": type(value).__name__,
        "repr": repr(value),
    }


def deserialize_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if isinstance(value, list):
        return [deserialize_value(item) for item in value]

    if not isinstance(value, dict):
        return value

    value_type = value.get(WIRE_TYPE_KEY)
    if value_type is None:
        return {key: deserialize_value(item) for key, item in value.items()}

    if value_type == "tuple":
        return tuple(deserialize_value(item) for item in value["items"])

    if value_type == "set":
        return set(deserialize_value(item) for item in value["items"])

    if value_type == "dict":
        return {deserialize_value(key): deserialize_value(item) for key, item in value["items"]}

    if value_type == "repr":
        return value["repr"]

    return value


@dataclass
class SandboxError:
    type: str
    message: str
    traceback: str | None = None

    @classmethod
    def from_exception(cls, exc: BaseException) -> "SandboxError":
        return cls(
            type=type(exc).__name__,
            message=str(exc),
            traceback="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )


@dataclass
class SandboxExecutionResult:
    output: Any = None
    logs: str = ""
    is_final_answer: bool = False
    error: SandboxError | None = None
    timed_out: bool = False


@dataclass
class SandboxRequest:
    request_id: str
    action: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str) -> "SandboxRequest":
        raw = json.loads(data)
        return cls(
            request_id=raw["request_id"],
            action=raw["action"],
            payload=raw.get("payload", {}),
        )


@dataclass
class SandboxResponse:
    request_id: str
    ok: bool
    payload: dict[str, Any] = field(default_factory=dict)
    error: SandboxError | None = None

    def to_json(self) -> str:
        data = asdict(self)
        return json.dumps(data, ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str) -> "SandboxResponse":
        raw = json.loads(data)
        error = raw.get("error")
        return cls(
            request_id=raw["request_id"],
            ok=raw["ok"],
            payload=raw.get("payload", {}),
            error=SandboxError(**error) if error else None,
        )
