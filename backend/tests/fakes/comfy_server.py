"""Loopback fake ComfyUI HTTP/WebSocket service for transport tests."""

from __future__ import annotations

import asyncio
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from typing import Any
from urllib.parse import parse_qs, urlparse

import websockets

from PIL import Image


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
        generation_behavior: str | None = None,
        output_count: int | None = None,
        corrupt_index: int | None = None,
        wrong_size_index: int | None = None,
        hold_after_prompt_count: int | None = None,
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
        self.generation_behavior = generation_behavior
        self.output_count = output_count
        self.corrupt_index = corrupt_index
        self.wrong_size_index = wrong_size_index
        self.hold_after_prompt_count = hold_after_prompt_count
        self.last_prompt: dict | None = None
        self.last_upload_name: str | None = None
        self.last_client_id: str | None = None
        self.last_view_params: dict[str, str] | None = None
        self.last_interrupt_prompt_id: str | None = None
        self.prompt_ids: list[str] = []
        self.interrupt_calls: list[str | None] = []
        self._prompt_counter = 0
        self._history_by_prompt: dict[str, dict] = {}
        self._outputs: dict[str, bytes] = {}
        self._held_prompts: set[str] = set()
        self._interrupted_prompts: set[str] = set()
        self.upload_names: list[str] = []
        self.prompt_payloads: list[dict] = []
        self.protocol_events: list[str] = []

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

    def _append_protocol_event(self, event: str) -> None:
        self.protocol_events.append(event)

    def _add_to_queue(self, prompt_id: str) -> None:
        if not any(
            len(item) > 1 and str(item[1]) == prompt_id
            for item in self.queue_running
        ):
            self.queue_running.append([0, prompt_id, {}, {}])

    def _remove_from_queue(self, prompt_id: str) -> None:
        self.queue_running[:] = [
            item
            for item in self.queue_running
            if not (len(item) > 1 and str(item[1]) == prompt_id)
        ]

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
            prompt_id = self.prompt_ids[-1] if self.prompt_ids else None
            if prompt_id is not None:
                if self.generation_behavior == "disconnect":
                    await websocket.close()
                    return
                if self.generation_behavior in {"execution-error", "oom"}:
                    detail = (
                        "CUDA out of memory"
                        if self.generation_behavior == "oom"
                        else "sampler failed"
                    )
                    self._remove_from_queue(prompt_id)
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "execution_error",
                                "data": {
                                    "prompt_id": prompt_id,
                                    "exception_message": detail,
                                },
                            }
                        )
                    )
                    await websocket.close()
                    return
                if self.generation_behavior == "timeout":
                    self._held_prompts.add(prompt_id)
                    while prompt_id in self._held_prompts and websocket.close_code is None:
                        await asyncio.sleep(0.01)
                if (
                    self.hold_after_prompt_count is not None
                    and len(self.prompt_ids) > self.hold_after_prompt_count
                ):
                    self._held_prompts.add(prompt_id)
                    while prompt_id in self._held_prompts and websocket.close_code is None:
                        await asyncio.sleep(0.01)
                if prompt_id in self._interrupted_prompts:
                    while websocket.close_code is None:
                        await asyncio.sleep(0.01)
                    return
                if prompt_id not in self._held_prompts and websocket.close_code is None:
                    self._remove_from_queue(prompt_id)
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "executed",
                                "data": {"prompt_id": prompt_id},
                            }
                        )
                    )
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
            prompt_id = path[len("/history/") :]
            history_entry = server._history_by_prompt.get(prompt_id)
            if history_entry is None:
                history_entry = (server.history or {}).get(prompt_id)
            self._json(
                {prompt_id: history_entry}
                if isinstance(history_entry, dict)
                else {}
            )
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
            server.last_view_params = {
                key: values[0] for key, values in query.items() if values
            }
            filename = query.get("filename", [""])[0]
            body = server._outputs.get(
                filename,
                server.view_bytes_by_name.get(filename, server.view_bytes),
            )
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
            if server.last_upload_name is not None:
                server.upload_names.append(server.last_upload_name)
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
            if server.generation_behavior is None:
                self._json(
                    {
                        "prompt_id": "11111111-2222-3333-4444-555555555555",
                        "number": 1,
                        "node_errors": {},
                    }
                )
                return
            prompt_id = f"prompt-{server._prompt_counter}"
            server._prompt_counter += 1
            server.prompt_ids.append(prompt_id)
            server.prompt_payloads.append(payload)
            server._append_protocol_event(f"prompt:{prompt_id}")
            server._add_to_queue(prompt_id)
            workflow = payload.get("prompt", {})
            batch_size = 1
            if isinstance(workflow, dict):
                for node in workflow.values():
                    if isinstance(node, dict) and isinstance(node.get("inputs"), dict):
                        value = node["inputs"].get(
                            "batch_size",
                            node["inputs"].get("amount"),
                        )
                        if isinstance(value, int):
                            batch_size = value
                            break
            count = batch_size if server.output_count is None else server.output_count
            images = []
            for index in range(count):
                filename = f"{prompt_id}-{index}.png"
                if index == server.corrupt_index:
                    body = b"corrupt"
                else:
                    size = 16 if index == server.wrong_size_index else 1024
                    image = Image.new("RGB", (size, size), (40 + index, 80, 120))
                    buffer = BytesIO()
                    image.save(buffer, format="PNG")
                    body = buffer.getvalue()
                server._outputs[filename] = body
                images.append({"filename": filename, "subfolder": "", "type": "output"})
            server._history_by_prompt[prompt_id] = {
                "outputs": {"19": {"images": images}}
            }
            self._json(
                {
                    "prompt_id": prompt_id,
                    "number": 1,
                    "node_errors": {},
                }
            )
            return
        if parsed.path == "/interrupt":
            length = int(self.headers.get("Content-Length", "0"))
            payload = {}
            if length:
                decoded = json.loads(self.rfile.read(length))
                if isinstance(decoded, dict):
                    payload = decoded
            prompt_id = payload.get("prompt_id")
            server.last_interrupt_prompt_id = (
                str(prompt_id) if prompt_id is not None else None
            )
            server.interrupt_calls.append(
                str(prompt_id) if prompt_id is not None else None
            )
            if prompt_id is not None:
                server._interrupted_prompts.add(str(prompt_id))
                server._held_prompts.discard(str(prompt_id))
                server._remove_from_queue(str(prompt_id))
                server._append_protocol_event(f"interrupt:{prompt_id}")
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
