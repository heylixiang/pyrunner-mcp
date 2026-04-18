from __future__ import annotations

import asyncio

from lib.mcp_server import MCPApp, ToolExecutionError, MCPServer, RequestContext


def _initialize(server: MCPServer) -> dict:
    response = asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1.0"},
                },
            }
        )
    )
    asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
        )
    )
    return response


def test_mcp_app_registers_tools_and_executes_them():
    mcp = MCPApp("Demo")

    @mcp.tool
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    server = mcp.create_server()
    initialize_response = _initialize(server)
    tools_response = asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            }
        )
    )
    call_response = asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "add",
                    "arguments": {"a": 2, "b": 3},
                },
            }
        )
    )

    assert initialize_response["result"]["serverInfo"]["name"] == "Demo"
    assert tools_response["result"]["tools"][0]["name"] == "add"
    assert call_response["result"]["isError"] is False
    assert call_response["result"]["structuredContent"]["result"] == 5


def test_mcp_app_returns_tool_execution_errors_as_tool_results():
    mcp = MCPApp("Demo")

    @mcp.tool
    def fail(name: str) -> dict[str, object]:
        """Always fail."""
        raise ToolExecutionError(
            f"Unknown name: {name}",
            structured_content={
                "ok": False,
                "kind": "demo_error",
                "error": {"message": f"Unknown name: {name}"},
            },
        )

    server = mcp.create_server()
    _initialize(server)
    response = asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "fail",
                    "arguments": {"name": "alice"},
                },
            }
        )
    )

    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"]["kind"] == "demo_error"


def test_mcp_app_accepts_meta_fields_on_mcp_requests():
    mcp = MCPApp("Demo")

    @mcp.tool
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    server = mcp.create_server()
    initialize_response = asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1.0"},
                    "_meta": {"client": "inspector"},
                },
            }
        )
    )
    asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
        )
    )
    tools_response = asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {
                    "_meta": {"source": "inspector"},
                },
            }
        )
    )
    call_response = asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "add",
                    "arguments": {"a": 4, "b": 5},
                    "_meta": {"source": "inspector"},
                },
            }
        )
    )

    assert initialize_response["result"]["serverInfo"]["name"] == "Demo"
    assert tools_response["result"]["tools"][0]["name"] == "add"
    assert call_response["result"]["structuredContent"]["result"] == 9


def test_mcp_app_rejects_non_object_meta_fields():
    mcp = MCPApp("Demo")
    server = mcp.create_server()

    response = asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1.0"},
                    "_meta": "invalid",
                },
            }
        )
    )

    assert response["error"]["code"] == -32602
    assert response["error"]["message"] == "params._meta must be an object."


def test_mcp_app_supports_resources_prompts_and_request_context():
    mcp = MCPApp("Demo")

    @mcp.tool
    def inspect_user(name: str, context: RequestContext | None = None) -> dict[str, object]:
        return {
            "name": name,
            "method": context.method if context is not None else None,
            "client": context.client_info["name"] if context and context.client_info else None,
        }

    @mcp.resource("memo://status")
    def status_resource() -> str:
        return "ready"

    @mcp.resource("greeting://{name}")
    def greeting_resource(name: str, context: RequestContext | None = None) -> str:
        return f"{context.method}:{name}" if context is not None else name

    @mcp.prompt
    def welcome(name: str, style: str = "friendly") -> str:
        return f"{style}:{name}"

    server = mcp.create_server()
    _initialize(server)

    tools_response = asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            }
        )
    )
    call_response = asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "inspect_user",
                    "arguments": {"name": "alice"},
                },
            }
        )
    )
    resources_response = asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "resources/list",
                "params": {},
            }
        )
    )
    templates_response = asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "resources/templates/list",
                "params": {},
            }
        )
    )
    read_response = asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "resources/read",
                "params": {"uri": "greeting://alice"},
            }
        )
    )
    prompts_response = asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "prompts/list",
                "params": {},
            }
        )
    )
    prompt_response = asyncio.run(
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "prompts/get",
                "params": {
                    "name": "welcome",
                    "arguments": {"name": "alice", "style": "formal"},
                },
            }
        )
    )

    tool_schema = tools_response["result"]["tools"][0]["inputSchema"]
    assert "context" not in tool_schema["properties"]
    assert tool_schema["required"] == ["name"]
    assert call_response["result"]["structuredContent"]["method"] == "tools/call"
    assert call_response["result"]["structuredContent"]["client"] == "pytest"
    assert resources_response["result"]["resources"] == [
        {
            "name": "status_resource",
            "title": "Status Resource",
            "uri": "memo://status",
        }
    ]
    assert templates_response["result"]["resourceTemplates"][0]["uriTemplate"] == "greeting://{name}"
    assert read_response["result"]["contents"][0]["text"] == "resources/read:alice"
    assert prompts_response["result"]["prompts"][0]["arguments"] == [
        {"name": "name", "required": True},
        {"name": "style", "required": False},
    ]
    assert prompt_response["result"]["messages"][0]["content"]["text"] == "formal:alice"


def test_mcp_app_handles_batch_requests_after_initialization():
    mcp = MCPApp("Demo")

    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    @mcp.prompt
    def summarize(topic: str) -> str:
        return f"Summary: {topic}"

    server = mcp.create_server()
    _initialize(server)

    response = asyncio.run(
        server.handle_message(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "prompts/list",
                    "params": {},
                },
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                },
            ]
        )
    )

    assert [item["id"] for item in response] == [2, 3]
    assert response[0]["result"]["tools"][0]["name"] == "add"
    assert response[1]["result"]["prompts"][0]["name"] == "summarize"
