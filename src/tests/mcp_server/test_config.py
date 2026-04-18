from __future__ import annotations

from core import Settings


def test_settings_build_runtime_options_from_pyrunner_env(monkeypatch):
    monkeypatch.setenv("PYRUNNER_MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("PYRUNNER_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("PYRUNNER_MCP_PORT", "9000")
    monkeypatch.setenv("PYRUNNER_MCP_PATH", "rpc")
    monkeypatch.setenv("PYRUNNER_MCP_ALLOWED_ORIGINS", "http://a.example, http://b.example")

    settings = Settings()
    options = settings.mcp

    assert options.transport == "stdio"
    assert options.host == "0.0.0.0"
    assert options.port == 9000
    assert options.path == "/rpc"
    assert options.allowed_origins == ["http://a.example", "http://b.example"]


def test_settings_support_mcp_aliases_and_defaults(monkeypatch):
    monkeypatch.delenv("PYRUNNER_MCP_TRANSPORT", raising=False)
    monkeypatch.delenv("PYRUNNER_MCP_HOST", raising=False)
    monkeypatch.delenv("PYRUNNER_MCP_PORT", raising=False)
    monkeypatch.delenv("PYRUNNER_MCP_PATH", raising=False)
    monkeypatch.delenv("PYRUNNER_MCP_ALLOWED_ORIGINS", raising=False)
    monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("MCP_HOST", "127.0.0.2")

    settings = Settings()
    options = settings.mcp

    assert options.transport == "streamable-http"
    assert options.host == "127.0.0.2"
    assert options.port == 8094
    assert options.path == "/mcp"
    assert options.allowed_origins is None
