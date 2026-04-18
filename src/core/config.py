from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from lib.mcp_server import MCPRuntimeOptions


REPO_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    transport: str = Field(
        default="streamable-http",
        validation_alias=AliasChoices("PYRUNNER_MCP_TRANSPORT", "MCP_TRANSPORT"),
    )
    host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("PYRUNNER_MCP_HOST", "MCP_HOST"),
    )
    port: int = Field(
        default=8094,
        validation_alias=AliasChoices("PYRUNNER_MCP_PORT", "MCP_PORT"),
    )
    path: str = Field(
        default="/mcp",
        validation_alias=AliasChoices("PYRUNNER_MCP_PATH", "MCP_PATH"),
    )
    allowed_origins_raw: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PYRUNNER_MCP_ALLOWED_ORIGINS", "MCP_ALLOWED_ORIGINS"),
    )
    authorized_imports_raw: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PYRUNNER_AUTHORIZED_IMPORTS"),
    )
    executor_timeout_seconds: float | None = Field(
        default=None,
        validation_alias=AliasChoices("PYRUNNER_EXECUTOR_TIMEOUT_SECONDS"),
    )
    sandbox_response_timeout_seconds: float = Field(
        default=5.0,
        validation_alias=AliasChoices("PYRUNNER_SANDBOX_RESPONSE_TIMEOUT_SECONDS"),
    )
    reload: bool = Field(
        default=False,
        validation_alias=AliasChoices("PYRUNNER_RELOAD"),
    )

    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def allowed_origins(self) -> list[str] | None:
        if self.allowed_origins_raw is None:
            return None
        values = [item.strip() for item in self.allowed_origins_raw.split(",") if item.strip()]
        return values or None

    @property
    def authorized_imports(self) -> list[str]:
        if self.authorized_imports_raw is None:
            return []
        return [item.strip() for item in self.authorized_imports_raw.split(",") if item.strip()]

    @property
    def mcp(self) -> MCPRuntimeOptions:
        return MCPRuntimeOptions(
            transport=self.transport,
            host=self.host,
            port=self.port,
            path=self.path,
            allowedOrigins=self.allowed_origins,
            reload=self.reload,
        )
