from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

SERVER_NOT_INITIALIZED = -32002


@dataclass
class MCPProtocolError(Exception):
    code: int
    message: str
    data: Any = None

    def __str__(self) -> str:
        return self.message


class MCPNotInitializedError(MCPProtocolError):
    def __init__(self) -> None:
        super().__init__(
            code=SERVER_NOT_INITIALIZED,
            message="Server not initialized. Call initialize first.",
        )
