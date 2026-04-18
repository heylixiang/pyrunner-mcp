from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import http.client
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_main_module_serves_http_transport_from_env():
    port = _find_free_port()
    env = dict(os.environ)
    pythonpath_parts = [str(REPO_ROOT / "src")]
    existing_pythonpath = env.get("PYTHONPATH")
    if existing_pythonpath:
        pythonpath_parts.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    env["PYRUNNER_MCP_TRANSPORT"] = "streamable-http"
    env["PYRUNNER_MCP_HOST"] = "127.0.0.1"
    env["PYRUNNER_MCP_PORT"] = str(port)
    env["PYRUNNER_MCP_PATH"] = "/mcp"

    process = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "src" / "apps" / "features" / "main.py")],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        _wait_for_healthcheck(f"http://127.0.0.1:{port}/healthz", process)

        initialize_response, initialize_headers = _post_json(
            f"http://127.0.0.1:{port}/mcp",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1.0"},
                },
            },
        )
        session_id = initialize_headers["Mcp-Session-Id"]
        _post_json(
            f"http://127.0.0.1:{port}/mcp",
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
            headers={"Mcp-Session-Id": session_id},
        )
        tools_response, _ = _post_json(
            f"http://127.0.0.1:{port}/mcp",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
            headers={"Mcp-Session-Id": session_id},
        )
        sse_status, sse_content_type, sse_first_line = _open_sse_stream(
            host="127.0.0.1",
            port=port,
            path="/mcp",
            session_id=session_id,
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    assert initialize_response["result"]["serverInfo"]["name"] == "PyRunner MCP"
    tool_names = {tool["name"] for tool in tools_response["result"]["tools"]}
    assert "execute_python_code" in tool_names
    assert sse_status == 200
    assert sse_content_type.startswith("text/event-stream")
    assert sse_first_line.startswith(": connected")


def test_main_module_http_rejects_invalid_accept_unknown_session_and_origin():
    port = _find_free_port()
    process = _start_http_process(port)

    try:
        _wait_for_healthcheck(f"http://127.0.0.1:{port}/healthz", process)

        bad_accept = _raw_http_request(
            host="127.0.0.1",
            port=port,
            method="POST",
            path="/mcp",
            body=json.dumps(
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
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        unknown_session = _raw_http_request(
            host="127.0.0.1",
            port=port,
            method="POST",
            path="/mcp",
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                }
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Mcp-Session-Id": "missing-session",
            },
        )
        bad_origin = _raw_http_request(
            host="127.0.0.1",
            port=port,
            method="POST",
            path="/mcp",
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "pytest", "version": "1.0"},
                    },
                }
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Origin": "https://evil.example",
            },
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    assert bad_accept[0] == 406
    assert bad_accept[1]["error"] == "Accept header must include application/json and text/event-stream"
    assert unknown_session[0] == 404
    assert unknown_session[1]["error"] == "Unknown MCP session"
    assert bad_origin[0] == 403
    assert bad_origin[1]["error"] == "Origin not allowed"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_http_process(port: int) -> subprocess.Popen[str]:
    env = dict(os.environ)
    pythonpath_parts = [str(REPO_ROOT / "src")]
    existing_pythonpath = env.get("PYTHONPATH")
    if existing_pythonpath:
        pythonpath_parts.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    env["PYRUNNER_MCP_TRANSPORT"] = "streamable-http"
    env["PYRUNNER_MCP_HOST"] = "127.0.0.1"
    env["PYRUNNER_MCP_PORT"] = str(port)
    env["PYRUNNER_MCP_PATH"] = "/mcp"
    return subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "src" / "apps" / "features" / "main.py")],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for_healthcheck(url: str, process: subprocess.Popen[str], timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise AssertionError(f"HTTP MCP server exited unexpectedly. stderr={stderr!r}")
        try:
            response = urllib.request.urlopen(url, timeout=0.5)
            if response.status == 200:
                return
        except Exception:
            time.sleep(0.1)
    stderr = process.stderr.read() if process.stderr is not None else ""
    raise AssertionError(f"Timed out waiting for HTTP MCP server. stderr={stderr!r}")


def _post_json(url: str, payload: dict, headers: dict[str, str] | None = None) -> tuple[dict, dict[str, str]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **(headers or {}),
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        body = response.read().decode("utf-8")
        response_headers = dict(response.headers.items())
    return (json.loads(body) if body else {}), response_headers


def _open_sse_stream(*, host: str, port: int, path: str, session_id: str) -> tuple[int, str, str]:
    connection = http.client.HTTPConnection(host, port, timeout=5)
    connection.request(
        "GET",
        path,
        headers={
            "Accept": "text/event-stream",
            "Mcp-Session-Id": session_id,
        },
    )
    response = connection.getresponse()
    first_line = response.fp.readline().decode("utf-8").strip()
    status = response.status
    content_type = response.getheader("Content-Type", "")
    connection.close()
    return status, content_type, first_line


def _raw_http_request(
    *,
    host: str,
    port: int,
    method: str,
    path: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    connection = http.client.HTTPConnection(host, port, timeout=5)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    payload = response.read().decode("utf-8")
    connection.close()
    return response.status, (json.loads(payload) if payload else {})
