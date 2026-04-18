from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lib.mcp_server.protocol.server import MCPServerSession


@dataclass(frozen=True)
class RequestContext:
    method: str
    params: dict[str, Any] | None
    request_id: str | int | float | None
    session: MCPServerSession
    server_name: str
    client_info: dict[str, Any] | None = None
    protocol_version: str | None = None
