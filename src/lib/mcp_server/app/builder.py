from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from lib.mcp_server.app.registries import PromptRegistry, ResourceRegistry, ToolRegistry
from lib.mcp_server.protocol.server import MCPServer, MCPServerConfig
from lib.mcp_server.transports.options import MCPRuntimeOptions
from lib.mcp_server.transports.stdio import StdioMCPServerTransport
from lib.mcp_server.transports.streamable_http import StreamableHTTPTransport


class MCPApp:
    def __init__(
        self,
        name: str,
        *,
        version: str = "0.1.0",
        instructions: str | None = None,
        title: str | None = None,
        supported_protocol_versions: tuple[str, ...] | None = None,
        lifespan: Callable[["MCPApp"], AsyncIterator[Any]] | None = None,
    ):
        self._tools = ToolRegistry()
        self._resources = ResourceRegistry()
        self._prompts = PromptRegistry()
        self._cleanup_callbacks: list[Callable[[], Any]] = []
        self._lifespan = lifespan
        self._config = MCPServerConfig(
            server_name=name,
            server_title=title or name,
            server_version=version,
            instructions=instructions or "",
            supported_protocol_versions=supported_protocol_versions,
        )

    def tool(
        self,
        func: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
        annotations: dict[str, Any] | None = None,
    ):
        def decorator(callback: Callable[..., Any]) -> Callable[..., Any]:
            self._tools.add_tool(
                callback,
                name=name,
                title=title,
                description=description,
                input_schema=input_schema,
                annotations=annotations,
            )
            return callback

        if func is None:
            return decorator
        return decorator(func)

    def resource(
        self,
        uri_template: str,
        func: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
        annotations: dict[str, Any] | None = None,
    ):
        def decorator(callback: Callable[..., Any]) -> Callable[..., Any]:
            self._resources.add_resource(
                callback,
                uri_template=uri_template,
                name=name,
                title=title,
                description=description,
                mime_type=mime_type,
                annotations=annotations,
            )
            return callback

        if func is None:
            return decorator
        return decorator(func)

    def prompt(
        self,
        func: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
    ):
        def decorator(callback: Callable[..., Any]) -> Callable[..., Any]:
            self._prompts.add_prompt(
                callback,
                name=name,
                title=title,
                description=description,
            )
            return callback

        if func is None:
            return decorator
        return decorator(func)

    def add_cleanup(self, callback: Callable[[], Any]) -> None:
        self._cleanup_callbacks.append(callback)

    def list_tools(self) -> list[dict[str, Any]]:
        return [tool.model_dump(exclude_none=True) for tool in self._tools.list_tools()]

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._tools.call_tool(name, arguments, context=None)

    def create_server(self) -> MCPServer:
        return MCPServer(
            tool_registry=self._tools,
            resource_registry=self._resources,
            prompt_registry=self._prompts,
            config=self._config,
            cleanup_callbacks=self._cleanup_callbacks,
        )

    async def run_async(self, options: MCPRuntimeOptions | None = None) -> int:
        _setup_mcp_logging()
        runtime_options = options or MCPRuntimeOptions()
        server = self.create_server()
        transport = _build_transport(server, runtime_options)
        async with self._run_lifespan():
            return await transport.serve_forever()

    def run(self, options: MCPRuntimeOptions | None = None) -> int:
        runtime_options = options or MCPRuntimeOptions()
        if runtime_options.reload and os.environ.get("_MCP_RUN_MAIN") != "1":
            return self._run_with_reload(runtime_options)
        return asyncio.run(self.run_async(options))

    def _run_with_reload(self, options: MCPRuntimeOptions) -> int:
        try:
            from watchfiles import PythonFilter, watch
        except ImportError:
            raise ImportError(
                "watchfiles is required for auto-reload. "
                "Install it with: uv add --group dev watchfiles"
            ) from None

        _setup_mcp_logging()
        logger = logging.getLogger("mcp.transport")
        watch_paths = options.reload_dirs or [os.getcwd()]

        logger.info(
            "Started reloader process [%d] using WatchFiles", os.getpid()
        )

        process: subprocess.Popen[bytes] | None = None
        try:
            while True:
                env = {**os.environ, "_MCP_RUN_MAIN": "1"}
                process = subprocess.Popen(sys.orig_argv, env=env)
                for changes in watch(
                    *watch_paths, watch_filter=PythonFilter()
                ):
                    for _, path in changes:
                        logger.warning(
                            "WatchFiles detected changes in '%s'. Reloading...",
                            os.path.relpath(path),
                        )
                    _terminate_process(process)
                    break
        except KeyboardInterrupt:
            pass
        finally:
            if process is not None:
                _terminate_process(process)

        return 0

    @asynccontextmanager
    async def _run_lifespan(self) -> AsyncIterator[None]:
        if self._lifespan is None:
            yield
            return

        async with self._lifespan(self):
            yield


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _build_transport(server: MCPServer, options: MCPRuntimeOptions):
    if options.transport == "stdio":
        return StdioMCPServerTransport(server)
    if options.transport in {"streamable-http", "http"}:
        return StreamableHTTPTransport(server, options=options)
    raise ValueError(f"Unsupported MCP transport: {options.transport}")


def _setup_mcp_logging() -> None:
    mcp_logger = logging.getLogger("mcp")
    if mcp_logger.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-5s %(name)s -- %(message)s",
        datefmt="%H:%M:%S",
    ))
    mcp_logger.addHandler(handler)
    mcp_logger.setLevel(logging.DEBUG)
