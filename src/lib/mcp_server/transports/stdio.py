from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, TextIO

from lib.mcp_server.protocol.errors import MCPProtocolError, PARSE_ERROR
from lib.mcp_server.protocol.models import make_error_response
from lib.mcp_server.protocol.server import MCPServer

_log = logging.getLogger("mcp.transport")


class StdioMCPServerTransport:
    def __init__(
        self,
        server: MCPServer,
        *,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
    ):
        self._server = server
        self._stdin = stdin or sys.stdin
        self._stdout = stdout or sys.stdout

    async def serve_forever(self) -> int:
        session = self._server.create_session()
        _log.info("listening on stdio")
        try:
            while True:
                raw_line = await asyncio.to_thread(self._stdin.readline)
                if raw_line == "":
                    return 0
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    self._write_message(
                        make_error_response(None, MCPProtocolError(PARSE_ERROR, f"Invalid JSON: {exc.msg}"))
                    )
                    continue

                response = await session.handle_message(payload)
                if response is not None:
                    self._write_message(response)
        finally:
            self._server.close()

    def _write_message(self, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
        self._stdout.write(json.dumps(payload, ensure_ascii=False))
        self._stdout.write("\n")
        self._stdout.flush()
