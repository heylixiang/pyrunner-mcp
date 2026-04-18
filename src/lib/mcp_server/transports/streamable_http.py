from __future__ import annotations

import asyncio
import hmac
import json
import logging
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from lib.mcp_server.protocol.errors import MCPProtocolError, PARSE_ERROR
from lib.mcp_server.protocol.models import make_error_response
from lib.mcp_server.protocol.server import MCPServer, MCPServerSession
from lib.mcp_server.transports.options import MCPRuntimeOptions


MCP_SESSION_HEADER = "mcp-session-id"

_log = logging.getLogger("mcp.transport")


@dataclass
class _HTTPRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


class StreamableHTTPTransport:
    def __init__(
        self,
        server: MCPServer,
        *,
        options: MCPRuntimeOptions | None = None,
        host: str = "127.0.0.1",
        port: int = 8000,
        path: str = "/mcp",
        allowed_origins: Iterable[str] | None = None,
        sse_keepalive_seconds: float = 15.0,
        bearer_tokens: Iterable[str] | None = None,
    ):
        runtime_options = options or MCPRuntimeOptions(
            transport="streamable-http",
            host=host,
            port=port,
            path=path,
            allowedOrigins=list(allowed_origins or []),
            sseKeepaliveSeconds=sse_keepalive_seconds,
            bearerTokens=list(bearer_tokens or []),
        )
        self._server = server
        self._host = runtime_options.host
        self._port = runtime_options.port
        self._path = runtime_options.path
        self._allowed_origins = set(runtime_options.allowed_origins or [])
        self._sse_keepalive_seconds = runtime_options.sse_keepalive_seconds
        self._bearer_tokens: list[str] = runtime_options.bearer_tokens or []
        self._sessions = _HTTPSessionStore(server)

    async def serve_forever(self) -> int:
        tcp_server = await asyncio.start_server(self._handle_client, self._host, self._port)
        sockets = tcp_server.sockets or []
        bind = sockets[0].getsockname() if sockets else (self._host, self._port)
        _log.info(
            "listening on http://%s:%s%s (streamable-http)",
            bind[0], bind[1], self._path,
        )
        try:
            async with tcp_server:
                await tcp_server.serve_forever()
        finally:
            self._server.close()
        return 0

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        response_writer = _HTTPResponseWriter(writer)
        try:
            request = await self._read_request(reader)
            if request is None:
                return
            await self._dispatch_request(request, reader, response_writer)
        except _HTTPError as exc:
            _log.debug("%s %s %d", request.method if request else "?", request.path if request else "?", exc.status)
            await response_writer.write_json_response(status=exc.status, body=exc.payload)
        except Exception as exc:  # pragma: no cover - defensive guard
            await response_writer.write_json_response(
                status=500,
                body={"error": f"Internal server error: {exc}"},
            )
        finally:
            if not writer.is_closing():
                writer.close()
                await writer.wait_closed()

    async def _dispatch_request(
        self,
        request: _HTTPRequest,
        reader: asyncio.StreamReader,
        response_writer: "_HTTPResponseWriter",
    ) -> None:
        if request.path == "/healthz" and request.method == "GET":
            await response_writer.write_json_response(status=200, body={"ok": True})
            return

        if request.path != self._path:
            await response_writer.write_json_response(status=404, body={"error": "Not found"})
            return

        if not self._is_bearer_token_valid(request.headers):
            await response_writer.write_json_response(
                status=401,
                body={"error": "Unauthorized"},
                headers={"WWW-Authenticate": "Bearer"},
            )
            return

        if not self._is_origin_allowed(request.headers):
            await response_writer.write_json_response(status=403, body={"error": "Origin not allowed"})
            return

        if request.method == "POST":
            await self._handle_post(request, response_writer)
            return
        if request.method == "GET":
            await self._handle_get(request, reader, response_writer)
            return
        if request.method == "DELETE":
            await self._handle_delete(request, response_writer)
            return

        await response_writer.write_json_response(status=405, body={"error": "Method not allowed"})

    async def _handle_post(self, request: _HTTPRequest, response_writer: "_HTTPResponseWriter") -> None:
        self._validate_post_accept(request.headers)
        payload = self._parse_json_body(request.body)
        if not isinstance(payload, (dict, list)):
            await response_writer.write_json_response(status=400, body={"error": "Invalid JSON-RPC payload"})
            return

        session_id = request.headers.get(MCP_SESSION_HEADER)
        session = self._sessions.get(session_id) if session_id is not None else None
        created_session_id: str | None = None
        if session_id is not None and session is None:
            await response_writer.write_json_response(status=404, body={"error": "Unknown MCP session"})
            return
        if session is None:
            messages = payload if isinstance(payload, list) else [payload]
            if not _contains_initialize_request(messages):
                await response_writer.write_json_response(status=400, body={"error": "Missing MCP session"})
                return
            created_session_id, session = self._sessions.create()

        response = await session.handle_message(payload)
        if created_session_id and not session.is_initialized():
            self._sessions.delete(created_session_id)
            created_session_id = None

        headers = {}
        active_session_id = created_session_id or session_id
        if active_session_id is not None:
            headers["Mcp-Session-Id"] = active_session_id

        if response is None:
            await response_writer.write_empty_response(status=202, headers=headers)
            return

        await response_writer.write_json_response(status=200, body=response, headers=headers)

    async def _handle_get(
        self,
        request: _HTTPRequest,
        reader: asyncio.StreamReader,
        response_writer: "_HTTPResponseWriter",
    ) -> None:
        if "text/event-stream" not in request.headers.get("accept", ""):
            await response_writer.write_json_response(status=405, body={"error": "SSE stream not requested"})
            return

        session_id = request.headers.get(MCP_SESSION_HEADER)
        if session_id is None:
            await response_writer.write_json_response(status=400, body={"error": "Missing MCP session"})
            return

        session = self._sessions.get(session_id)
        if session is None:
            await response_writer.write_json_response(status=404, body={"error": "Unknown MCP session"})
            return

        _ = session
        await response_writer.write_sse_headers()
        await response_writer.write_sse_comment("connected")

        try:
            while not reader.at_eof():
                await asyncio.sleep(self._sse_keepalive_seconds)
                await response_writer.write_sse_comment("keepalive")
        except (ConnectionResetError, BrokenPipeError):
            return

    async def _handle_delete(self, request: _HTTPRequest, response_writer: "_HTTPResponseWriter") -> None:
        session_id = request.headers.get(MCP_SESSION_HEADER)
        if session_id is None:
            await response_writer.write_json_response(status=400, body={"error": "Missing MCP session"})
            return
        if not self._sessions.delete(session_id):
            await response_writer.write_json_response(status=404, body={"error": "Unknown MCP session"})
            return
        await response_writer.write_empty_response(status=204)

    async def _read_request(self, reader: asyncio.StreamReader) -> _HTTPRequest | None:
        request_line = await reader.readline()
        if not request_line:
            return None
        parts = request_line.decode("utf-8").strip().split(" ")
        if len(parts) != 3:
            raise ValueError("Invalid HTTP request line")
        method, path, _ = parts

        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            name, value = line.decode("utf-8").split(":", 1)
            headers[name.strip().lower()] = value.strip()

        content_length = int(headers.get("content-length", "0"))
        body = await reader.readexactly(content_length) if content_length else b""
        return _HTTPRequest(method=method.upper(), path=path, headers=headers, body=body)

    def _parse_json_body(self, body: bytes) -> Any:
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise _HTTPError(
                400,
                make_error_response(None, MCPProtocolError(PARSE_ERROR, f"Invalid JSON: {exc.msg}")),
            ) from exc

    def _validate_post_accept(self, headers: dict[str, str]) -> None:
        accept = headers.get("accept", "")
        if "application/json" not in accept or "text/event-stream" not in accept:
            raise _HTTPError(
                406,
                {"error": "Accept header must include application/json and text/event-stream"},
            )

    def _is_bearer_token_valid(self, headers: dict[str, str]) -> bool:
        if not self._bearer_tokens:
            return True
        auth = headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return False
        provided = auth[7:]
        return any(
            hmac.compare_digest(provided.encode(), token.encode())
            for token in self._bearer_tokens
        )

    def _is_origin_allowed(self, headers: dict[str, str]) -> bool:
        origin = headers.get("origin")
        if not origin:
            return True

        parsed = urlparse(origin)
        origin_value = f"{parsed.scheme}://{parsed.netloc}"
        if origin_value in self._allowed_origins:
            return True

        allowed_hosts = {self._host, "127.0.0.1", "localhost"}
        if parsed.hostname in allowed_hosts:
            return True
        return False


class _HTTPSessionStore:
    def __init__(self, server: MCPServer):
        self._server = server
        self._sessions: dict[str, MCPServerSession] = {}

    def create(self) -> tuple[str, MCPServerSession]:
        session_id = secrets.token_urlsafe(24)
        session = self._server.create_session()
        self._sessions[session_id] = session
        return session_id, session

    def get(self, session_id: str | None) -> MCPServerSession | None:
        if session_id is None:
            return None
        return self._sessions.get(session_id)

    def delete(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None


class _HTTPResponseWriter:
    def __init__(self, writer: asyncio.StreamWriter):
        self._writer = writer

    async def write_json_response(
        self,
        *,
        status: int,
        body: dict[str, Any] | list[dict[str, Any]],
        headers: dict[str, str] | None = None,
    ) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        response_headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(payload)),
            "Connection": "close",
        }
        if headers:
            response_headers.update(headers)
        await self._write_response(status, response_headers, payload)

    async def write_empty_response(self, *, status: int, headers: dict[str, str] | None = None) -> None:
        response_headers = {"Content-Length": "0", "Connection": "close"}
        if headers:
            response_headers.update(headers)
        await self._write_response(status, response_headers, b"")

    async def write_sse_headers(self) -> None:
        await self._write_response(
            200,
            {
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
            None,
        )

    async def write_sse_comment(self, comment: str) -> None:
        self._writer.write(f": {comment}\n\n".encode("utf-8"))
        await self._writer.drain()

    async def _write_response(self, status: int, headers: dict[str, str], body: bytes | None) -> None:
        reason = _HTTP_STATUS_REASONS.get(status, "OK")
        self._writer.write(f"HTTP/1.1 {status} {reason}\r\n".encode("utf-8"))
        for key, value in headers.items():
            self._writer.write(f"{key}: {value}\r\n".encode("utf-8"))
        self._writer.write(b"\r\n")
        if body:
            self._writer.write(body)
        await self._writer.drain()


class _HTTPError(Exception):
    def __init__(self, status: int, payload: dict[str, Any] | list[dict[str, Any]]):
        self.status = status
        self.payload = payload
        super().__init__(status)


def _contains_initialize_request(messages: list[Any]) -> bool:
    for message in messages:
        if isinstance(message, dict) and message.get("method") == "initialize":
            return True
    return False


_HTTP_STATUS_REASONS = {
    200: "OK",
    202: "Accepted",
    204: "No Content",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    406: "Not Acceptable",
    500: "Internal Server Error",
}
