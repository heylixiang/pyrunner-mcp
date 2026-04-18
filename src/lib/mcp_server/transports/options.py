from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MCPRuntimeOptions(BaseModel):
    transport: Literal["stdio", "streamable-http", "http"] = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    path: str = "/mcp"
    allowed_origins: list[str] | None = Field(default=None, alias="allowedOrigins")
    sse_keepalive_seconds: float = Field(default=15.0, alias="sseKeepaliveSeconds")
    bearer_tokens: list[str] | None = Field(default=None, alias="bearerTokens")
    reload: bool = False
    reload_dirs: list[str] | None = Field(default=None, alias="reloadDirs")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("path")
    @classmethod
    def _normalize_path(cls, value: str) -> str:
        return value if value.startswith("/") else f"/{value}"
