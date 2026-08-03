"""Loopback fake ComfyUI HTTP/WebSocket service for transport tests."""

from __future__ import annotations

import asyncio
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import websockets


class FakeComfyServer:
    def __init__(
        self,
        *,
        system_stats: dict | None = None,
        object_info: dict | None = None,
        prompt_behavior: str = "ok",
        history: dict | None = None,
        history_behavior: str = "ok",
        view_bytes: bytes = b"png-bytes",
        view_bytes_by_name: dict[str, bytes] | None = None,
        view_behavior: str = "ok",
        queue_running: list[list[Any]] | None = None,
        queue_pending: list[list[Any]] | None = None,
        ws_script: list[dict] | None = None,
        ws_disconnect: bool = False,
        ws_hold: bool = False,
        upload_behavior: str = "ok",
    ) -> None:
        self.system_stats = system_stats or {
            "system": {"comfyui_version": "0.29.2"}
        }
        self.object_info = object_info or {
            "CheckpointLoaderSimple": {"input": {}},
        }
        self.prompt_behavior = prompt_behavior
        self.history = history
        self.history_behavior = history_behavior
        self.view_bytes = view_bytes
        self.view_bytes_by_name = view_bytes_by_name or {}
        self.view_behavior = view_behavior
        self.queue_running = queue_running or []
        self.queue_pending = queue_pending or []
        self.ws_script = ws_script or []
        self.ws_disconnect = ws_disconnect
        self.ws_hold = ws_hold
        self.upload_behavior = upload_behavior
        self.last_prompt: dict | None = None
        self.last_upload_name: str | None = None
        self.last_client_id: str | None = None
        self.last_interrupt_prompt_id: str | None = None

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _HttpHandler)
        self._httpd.fake_server = self  # type: ignore[attr-defined]
        self._http_thread = threading.Thread(
            target=self._httpd.serve_forever,
            daemon=True,
        )
        self._http_thread.start()

        self._loop = asyncio.new_event_loop()
        self._ws_port: int | None = None
        self._ws_server = None
        self._ws_thread = threading.Thread(
            target=self._run_ws_loop,
            daemon=True,
        )
        self._ws_thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address
        return f"http://{host}:{port}"

    def _run_ws_loop(self) -> None:
        asyncio.set_event_loop(self._loop)

        async def handler(websocket) -> None:
            self.last_client_id = urlparse(websocket.request.path).query
            if self.ws_disconnect:
                await websocket.close()
                return
            if self.ws_hold:
                while not websocket.close_code:
                    await asyncio.sleep(0.05)
                await websocket.close()
                return
            for message in self.ws_script:
                await websocket.send(json.dumps(message))
            await websocket.close()

        async def serve() -> None:
            self._ws_server = await websockets.serve(
                handler,
                "127.0.0.1",
                0,
            )
            self._ws_port = self._ws_server.sockets[0].getsockname()[1]

        self._loop.run_until_complete(serve())
        self._loop.run_forever()

    @property
    def ws_url(self) -> str:
        while self._ws_port is None:
            threading.Event().wait(0.01)
        return f"ws://127.0.0.1:{self._ws_port}/ws"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._http_thread.join(timeout=5)
        if self._ws_server is not None:
            async def shutdown() -> None:
                self._ws_server.close()
                await self._ws_server.wait_closed()

            try:
                asyncio.run_coroutine_threadsafe(
                    shutdown(),
                    self._loop,
                ).result(timeout=5)
            except Exception:
                pass
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except RuntimeError:
            pass
        self._ws_thread.join(timeout=5)
        try:
            self._loop.close()
        except Exception:
            pass

    def __enter__(self) -> "FakeComfyServer":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()


class _HttpHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self) -> None:  # noqa: N802
        server: FakeComfyServer = self.server.fake_server  # type: ignore[attr-defined]
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/system_stats":
            self._json(server.system_stats)
            return
        if path == "/object_info":
            self._json(server.object_info)
            return
        if path.startswith("/history/"):
            if server.history_behavior == "missing":
                self._json({})
                return
            if server.history_behavior == "malformed":
                self._send_bytes(b"not json")
                return
            self._json(server.history or {})
            return
        if path == "/queue":
            self._json(
                {
                    "queue_running": server.queue_running,
                    "queue_pending": server.queue_pending,
                }
            )
            return
        if path == "/view":
            if server.view_behavior == "missing":
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            query = parse_qs(parsed.query, keep_blank_values=True)
            filename = query.get("filename", [""])[0]
            body = server.view_bytes_by_name.get(filename, server.view_bytes)
            self._send_bytes(body)
            return
        if path == "/interrupt":
            self._json({})
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        server: FakeComfyServer = self.server.fake_server  # type: ignore[attr-defined]
        parsed = urlparse(self.path)
        if parsed.path == "/upload/image":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            match = re.search(
                rb'filename="([^"]+)"',
                body[:4096],
            )
            server.last_upload_name = (
                match.group(1).decode("utf-8", "replace") if match else None
            )
            if server.upload_behavior == "reject":
                self.send_response(400)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._json({"name": server.last_upload_name})
            return
        if parsed.path == "/prompt":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                self.send_response(400)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            server.last_prompt = payload
            server.last_client_id = str(payload.get("client_id"))
            if server.prompt_behavior == "queue-error":
                self._json(
                    {"error": {"type": "invalid_prompt", "message": "bad"}},
                    status=400,
                )
                return
            if server.prompt_behavior == "malformed":
                self._send_bytes(b"not json")
                return
            self._json(
                {
                    "prompt_id": "11111111-2222-3333-4444-555555555555",
                    "number": 1,
                    "node_errors": {},
                }
            )
            return
        if parsed.path == "/interrupt":
            length = int(self.headers.get("Content-Length", "0"))
            payload = {}
            if length:
                payload = json.loads(self.rfile.read(length))
            prompt_id = payload.get("prompt_id")
            server.last_interrupt_prompt_id = (
                str(prompt_id) if prompt_id is not None else None
            )
            self._json({})
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, payload: dict, *, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.connection.close()

    def _send_bytes(self, body: bytes, *, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.connection.close()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return
