"""Loopback HTTP artifact server with scriptable download behaviors."""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class ArtifactServer:
    def __init__(self, payload: bytes, *, mode: str = "ok") -> None:
        self.payload = payload
        self.mode = mode
        self.redirect_target = ""
        self.delay = 0.0
        self.request_count = 0
        self.last_range_header: str | None = None
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.artifact_server = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address
        return f"http://{host}:{port}"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> "ArtifactServer":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self) -> None:  # noqa: N802
        server: ArtifactServer = self.server.artifact_server  # type: ignore[attr-defined]
        server.request_count += 1
        server.last_range_header = self.headers.get("Range")
        payload = server.payload

        if self.path == "/final.bin":
            self._send_payload(payload)
            return
        if server.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", server.redirect_target)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if server.mode == "status-500":
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if server.mode == "timeout":
            time.sleep(server.delay)
            self._send_payload(payload)
            return
        if server.mode == "wrong-hash":
            self._send_payload(b"X" + payload[1:])
            return
        if server.mode == "truncated":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload) + 100))
            self.end_headers()
            self.wfile.write(payload)
            self._close_after_body()
            return
        if server.mode == "oversized":
            extra = b"EXTRA-BYTES"
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload) + len(extra)))
            self.end_headers()
            self.wfile.write(payload + extra)
            self._close_after_body()
            return
        if server.mode == "disconnect":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload[: len(payload) // 2])
            self.wfile.flush()
            self.connection.close()
            return
        if server.mode == "slow":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            for index in range(0, len(payload), 1024):
                self.wfile.write(payload[index : index + 1024])
                self.wfile.flush()
                if server.delay:
                    time.sleep(server.delay)
            self._close_after_body()
            return
        if server.mode == "range":
            range_header = self.headers.get("Range")
            if range_header and range_header.startswith("bytes="):
                start_text = range_header.split("=", 1)[1].split("-", 1)[0]
                start = int(start_text)
                body = payload[start:]
                self.send_response(206)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header(
                    "Content-Range",
                    f"bytes {start}-{len(payload) - 1}/{len(payload)}",
                )
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                self._close_after_body()
                return
            self._send_payload(payload)
            return
        if server.mode == "ignore-range":
            self._send_payload(payload)
            return
        if server.mode == "wrong-content-range":
            body = payload[:6]
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Range", "bytes 0-5/999")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self._close_after_body()
            return
        self._send_payload(payload)

    def _send_payload(self, payload: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        self._close_after_body()

    def _close_after_body(self) -> None:
        try:
            self.wfile.flush()
        except OSError:
            pass
        self.connection.close()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return
