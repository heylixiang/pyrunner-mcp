from __future__ import annotations

import json
import os
import select
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


class MCPTestClient:
    def __init__(self):
        env = dict(os.environ)
        pythonpath_parts = [str(REPO_ROOT / "src")]
        existing_pythonpath = env.get("PYTHONPATH")
        if existing_pythonpath:
            pythonpath_parts.append(existing_pythonpath)
        env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
        env["PYRUNNER_MCP_TRANSPORT"] = "stdio"
        self._process = subprocess.Popen(
            [sys.executable, str(REPO_ROOT / "src" / "apps" / "features" / "main.py")],
            cwd=REPO_ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._next_id = 1

    def close(self) -> None:
        if self._process.stdin is not None:
            self._process.stdin.close()
        try:
            self._process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=3)

    def request(self, method: str, params: dict | None = None) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
        }
        self._next_id += 1
        if params is not None:
            payload["params"] = params
        self._write_message(payload)
        return self._read_message()

    def notify(self, method: str, params: dict | None = None) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        self._write_message(payload)

    def _write_message(self, payload: dict) -> None:
        assert self._process.stdin is not None
        self._process.stdin.write(json.dumps(payload, ensure_ascii=False))
        self._process.stdin.write("\n")
        self._process.stdin.flush()

    def _read_message(self, timeout: float = 5.0) -> dict:
        assert self._process.stdout is not None
        ready, _, _ = select.select([self._process.stdout], [], [], timeout)
        if not ready:
            stderr = ""
            if self._process.poll() is not None and self._process.stderr is not None:
                stderr = self._process.stderr.read()
            raise AssertionError(f"Timed out waiting for MCP response. stderr={stderr!r}")
        line = self._process.stdout.readline()
        if not line:
            stderr = self._process.stderr.read() if self._process.stderr is not None else ""
            raise AssertionError(f"MCP server exited unexpectedly. stderr={stderr!r}")
        return json.loads(line)

    def __enter__(self) -> "MCPTestClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def _initialize(client: MCPTestClient) -> dict:
    response = client.request(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1.0"},
        },
    )
    client.notify("notifications/initialized")
    return response


def test_main_module_initializes_and_lists_tools():
    with MCPTestClient() as client:
        initialize_response = _initialize(client)
        tools_response = client.request("tools/list", {})

    assert initialize_response["result"]["serverInfo"]["name"] == "PyRunner MCP"
    tool_names = {tool["name"] for tool in tools_response["result"]["tools"]}
    assert tool_names == {
        "execute_python_code",
    }


def test_main_module_accepts_meta_fields_from_clients():
    with MCPTestClient() as client:
        initialize_response = client.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0"},
                "_meta": {"client": "inspector"},
            },
        )
        client.notify("notifications/initialized")
        tools_response = client.request("tools/list", {"_meta": {"source": "inspector"}})

    assert initialize_response["result"]["serverInfo"]["name"] == "PyRunner MCP"
    assert "execute_python_code" in {tool["name"] for tool in tools_response["result"]["tools"]}


def test_main_module_manages_python_sessions_over_stdio():
    with MCPTestClient() as client:
        _initialize(client)

        # First call without sessionId: auto-creates a session
        first_response = client.request(
            "tools/call",
            {
                "name": "execute_python_code",
                "arguments": {
                    "code": "x = 10\nx",
                },
            },
        )
        session_id = first_response["result"]["structuredContent"]["sessionId"]

        # Second call with sessionId: reuses the session
        second_response = client.request(
            "tools/call",
            {
                "name": "execute_python_code",
                "arguments": {
                    "sessionId": session_id,
                    "code": "x + 5",
                },
            },
        )

    first_result = first_response["result"]["structuredContent"]["result"]
    second_result = second_response["result"]["structuredContent"]["result"]

    assert first_response["result"]["isError"] is False
    assert first_result["output"] == 10
    assert session_id is not None
    assert second_response["result"]["isError"] is False
    assert second_result["output"] == 15


def test_main_module_reports_protocol_and_tool_errors():
    with MCPTestClient() as client:
        before_initialize = client.request("tools/list", {})
        _initialize(client)
        unknown_tool = client.request(
            "tools/call",
            {
                "name": "missing_tool",
                "arguments": {},
            },
        )
        missing_session = client.request(
            "tools/call",
            {
                "name": "execute_python_code",
                "arguments": {
                    "sessionId": "missing-session",
                    "code": "1 + 1",
                },
            },
        )
        invalid_params = client.request(
            "tools/call",
            {
                "name": "execute_python_code",
                "arguments": {
                    "sessionId": "missing-session",
                    "code": "1 + 1",
                    "responseTimeoutSeconds": "fast",
                },
            },
        )

    assert before_initialize["error"]["code"] == -32002
    assert unknown_tool["error"]["code"] == -32602
    assert missing_session["result"]["isError"] is True
    assert missing_session["result"]["structuredContent"]["error"]["type"] == "SessionNotFoundError"
    assert invalid_params["error"]["code"] == -32602
