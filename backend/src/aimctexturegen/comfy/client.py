"""Generic ComfyUI HTTP/WebSocket protocol client."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from aimctexturegen.comfy.errors import (
    ComfyCanceledError,
    ComfyDisconnectedError,
    ComfyError,
    ComfyExecutionError,
    ComfyProtocolError,
    ComfyQueueError,
    ComfyTimeoutError,
    ComfyUnsafeInputError,
    ComfyUnsafeOutputError,
)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "[::1]"})


@dataclass(frozen=True)
class ComfyOutputImage:
    filename: str
    subfolder: str
    type: Literal["output"]


@dataclass(frozen=True)
class QueueSnapshot:
    running_prompt_ids: tuple[str, ...]
    pending_prompt_ids: tuple[str, ...]


def _default_ws_connect(url: str):
    from websockets.sync.client import connect

    return connect(url)


class ComfyClient:
    """Own only the ComfyUI protocol; never product workflows or paths."""

    def __init__(
        self,
        base_url: str,
        *,
        client_id: str | None = None,
        http: httpx.Client | None = None,
        ws_connect: Callable[[str], Any] | None = None,
        ws_base_url: str | None = None,
    ) -> None:
        for candidate in (base_url, ws_base_url):
            if candidate is None:
                continue
            parts = urlsplit(candidate)
            host = parts.hostname
            if parts.scheme not in {"http", "ws"} or host is None:
                raise ValueError("base URL must be an http loopback URL")
            if host not in _LOOPBACK_HOSTS:
                raise ValueError("base URL must target the loopback interface")
        self._base_url = base_url.rstrip("/")
        if ws_base_url is not None and not ws_base_url.startswith("ws"):
            raise ValueError("base URL must be an http loopback URL")
        self._ws_base_url = (ws_base_url or base_url).rstrip("/")
        self.client_id = client_id or str(uuid4())
        self._http = http or httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0),
            follow_redirects=False,
        )
        self._ws_connect = ws_connect or _default_ws_connect

    def system_stats(self) -> dict:
        return self._get_json("/system_stats")

    def object_info(self) -> dict:
        return self._get_json("/object_info")

    def upload_image(self, data: bytes, filename: str) -> dict:
        if not isinstance(data, bytes):
            raise ComfyUnsafeInputError("upload body must be bytes")
        if len(data) > MAX_UPLOAD_BYTES:
            raise ComfyUnsafeInputError("upload exceeds the size limit")
        name = _safe_upload_name(filename)
        response = self._http.post(
            f"{self._base_url}/upload/image",
            files={"image": (name, data)},
        )
        return self._json_response(response)

    def submit_prompt(self, workflow: dict) -> str:
        if not isinstance(workflow, dict):
            raise ComfyUnsafeInputError("workflow must be a JSON object")
        payload = deepcopy(workflow)
        response = self._http.post(
            f"{self._base_url}/prompt",
            json={"prompt": payload, "client_id": self.client_id},
        )
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ComfyProtocolError("response exceeds the size limit")
        try:
            data = response.json()
        except ValueError as exc:
            raise ComfyProtocolError("response is not JSON") from exc
        if not isinstance(data, dict):
            raise ComfyProtocolError("response must be a JSON object")
        if response.status_code == 400 and "error" in data:
            error = data["error"]
            raise ComfyQueueError(
                str(error.get("message", "prompt rejected by queue"))
            )
        if response.status_code != 200 or "prompt_id" not in data:
            raise ComfyProtocolError("prompt submission returned an invalid response")
        return str(data["prompt_id"])

    def get_history(self, prompt_id: str) -> dict:
        data = self._get_json(f"/history/{prompt_id}")
        entry = data.get(prompt_id)
        return entry if isinstance(entry, dict) else {}

    def get_output(self, history_entry: dict, filename: str) -> bytes:
        declared = _declared_outputs(history_entry)
        if filename not in declared:
            raise ComfyUnsafeOutputError(
                "output file was not declared by prompt history"
            )
        metadata = declared[filename]
        response = self._http.get(
            f"{self._base_url}/view",
            params={
                "filename": filename,
                "subfolder": metadata.get("subfolder", ""),
                "type": metadata.get("type", "output"),
            },
        )
        if response.status_code != 200:
            raise ComfyProtocolError("output retrieval failed")
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ComfyProtocolError("output exceeds the response limit")
        return response.content

    def declared_output_images(
        self,
        history_entry: dict,
        *,
        output_node_id: str,
    ) -> tuple[ComfyOutputImage, ...]:
        outputs = history_entry.get("outputs")
        if not isinstance(outputs, dict):
            raise ComfyUnsafeOutputError("history outputs must be an object")
        if output_node_id not in outputs:
            raise ComfyUnsafeOutputError(
                f"missing declared output node {output_node_id}"
            )
        node_output = outputs[output_node_id]
        if not isinstance(node_output, dict):
            raise ComfyUnsafeOutputError("declared output node is malformed")
        images = node_output.get("images")
        if not isinstance(images, list):
            raise ComfyUnsafeOutputError("declared output images must be a list")
        declared: list[ComfyOutputImage] = []
        seen: set[tuple[str, str, str]] = set()
        for image in images:
            descriptor = _parse_output_descriptor(image)
            key = (descriptor.filename, descriptor.subfolder, descriptor.type)
            if key in seen:
                raise ComfyUnsafeOutputError("duplicate declared output image")
            seen.add(key)
            declared.append(descriptor)
        return tuple(declared)

    def get_output_image(self, image: ComfyOutputImage) -> bytes:
        descriptor = _parse_output_descriptor(
            {
                "filename": image.filename,
                "subfolder": image.subfolder,
                "type": image.type,
            }
        )
        response = self._http.get(
            f"{self._base_url}/view",
            params={
                "filename": descriptor.filename,
                "subfolder": descriptor.subfolder,
                "type": descriptor.type,
            },
        )
        if response.status_code != 200:
            raise ComfyProtocolError("output retrieval failed")
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ComfyProtocolError("output exceeds the response limit")
        return response.content

    def queue_snapshot(self) -> QueueSnapshot:
        payload = self._get_json("/queue")
        return QueueSnapshot(
            running_prompt_ids=_queue_prompt_ids(payload, "queue_running"),
            pending_prompt_ids=_queue_prompt_ids(payload, "queue_pending"),
        )

    def interrupt(self, prompt_id: str | None = None) -> None:
        payload = {"prompt_id": prompt_id} if prompt_id is not None else None
        response = self._http.post(f"{self._base_url}/interrupt", json=payload)
        if response.status_code != 200:
            raise ComfyProtocolError("interrupt request failed")

    def wait_completion(
        self,
        prompt_id: str,
        *,
        timeout: float = 60.0,
        progress: Callable[[int, int], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> dict:
        ws_base = self._ws_base_url.replace("http://", "ws://", 1).replace(
            "https://",
            "wss://",
            1,
        )
        url = f"{ws_base}/ws?clientId={self.client_id}"
        deadline = time.monotonic() + timeout
        completed = False
        try:
            with self._ws_connect(url) as websocket:
                while time.monotonic() < deadline:
                    if cancel_requested is not None and cancel_requested():
                        raise ComfyCanceledError("completion wait was canceled")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ComfyTimeoutError(
                            "completion wait exceeded the deadline"
                        )
                    try:
                        raw = websocket.recv(timeout=min(remaining, 0.25))
                    except TimeoutError:
                        continue
                    except Exception as exc:
                        raise ComfyDisconnectedError(
                            "WebSocket disconnected before completion"
                        ) from exc
                    try:
                        message = json.loads(raw)
                    except (TypeError, ValueError) as exc:
                        raise ComfyProtocolError(
                            "malformed WebSocket message"
                        ) from exc
                    if not isinstance(message, dict) or not isinstance(
                        message.get("data"), dict
                    ):
                        raise ComfyProtocolError(
                            "malformed WebSocket message"
                        )
                    kind = message.get("type")
                    data = message["data"]
                    if kind == "execution_error":
                        raise ComfyExecutionError(
                            str(
                                data.get(
                                    "exception_message",
                                    "execution failed",
                                )
                            )
                        )
                    if kind == "executed" and data.get("prompt_id") == prompt_id:
                        completed = True
                        break
                    if (
                        kind == "progress"
                        and data.get("prompt_id") == prompt_id
                        and progress is not None
                    ):
                        progress(
                            int(data.get("value", 0)),
                            int(data.get("max", 0)),
                        )
                if not completed:
                    raise ComfyTimeoutError(
                        "completion wait exceeded the deadline"
                    )
        except ComfyError:
            raise
        history = self.get_history(prompt_id)
        if not history:
            raise ComfyProtocolError(
                "history reconciliation found no entry for the prompt"
            )
        return history

    def close(self) -> None:
        self._http.close()

    def _get_json(self, path: str) -> dict:
        response = self._http.get(f"{self._base_url}{path}")
        return self._json_response(response)

    def _json_response(self, response: httpx.Response) -> dict:
        if response.status_code != 200:
            raise ComfyProtocolError(
                f"unexpected HTTP status {response.status_code}"
            )
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ComfyProtocolError("response exceeds the size limit")
        try:
            data = response.json()
        except ValueError as exc:
            raise ComfyProtocolError("response is not JSON") from exc
        if not isinstance(data, dict):
            raise ComfyProtocolError("response must be a JSON object")
        return data


def _safe_upload_name(filename: str) -> str:
    if not isinstance(filename, str):
        raise ComfyUnsafeInputError("upload filename must be a string")
    normalized = filename.replace("\\", "/")
    segments = normalized.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ComfyUnsafeInputError("upload filename contains an unsafe path")
    name = segments[-1]
    if (
        not name
        or name in {".", ".."}
        or name.startswith(".")
        or len(name) > 255
        or any(ord(character) < 32 for character in name)
    ):
        raise ComfyUnsafeInputError("upload filename is unsafe")
    return name


def _declared_outputs(history_entry: dict) -> dict[str, dict]:
    declared: dict[str, dict] = {}
    outputs = history_entry.get("outputs")
    if not isinstance(outputs, dict):
        return declared
    for node_output in outputs.values():
        if not isinstance(node_output, dict):
            continue
        for image in node_output.get("images", []):
            if not isinstance(image, dict):
                continue
            filename = image.get("filename")
            if isinstance(filename, str) and filename:
                declared[filename] = image
    return declared


def _safe_output_subfolder(subfolder: str) -> str:
    if not isinstance(subfolder, str):
        raise ComfyUnsafeOutputError("declared output subfolder is unsafe")
    if subfolder == "":
        return ""
    normalized = subfolder.replace("\\", "/")
    segments = normalized.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ComfyUnsafeOutputError("declared output subfolder is unsafe")
    if any(any(ord(character) < 32 for character in segment) for segment in segments):
        raise ComfyUnsafeOutputError("declared output subfolder is unsafe")
    return normalized


def _safe_output_filename(filename: str) -> str:
    if not isinstance(filename, str):
        raise ComfyUnsafeOutputError("declared output filename is unsafe")
    normalized = filename.replace("\\", "/")
    segments = normalized.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ComfyUnsafeOutputError("declared output filename is unsafe")
    name = segments[-1]
    if (
        not name
        or name.startswith(".")
        or len(name) > 255
        or any(ord(character) < 32 for character in name)
    ):
        raise ComfyUnsafeOutputError("declared output filename is unsafe")
    return name


def _parse_output_descriptor(image: Any) -> ComfyOutputImage:
    if not isinstance(image, dict):
        raise ComfyUnsafeOutputError("declared output image is malformed")
    filename = image.get("filename")
    image_type = image.get("type")
    if not isinstance(filename, str) or not filename:
        raise ComfyUnsafeOutputError("declared output filename is unsafe")
    if image_type != "output":
        raise ComfyUnsafeOutputError("declared output type is unsafe")
    return ComfyOutputImage(
        filename=_safe_output_filename(filename),
        subfolder=_safe_output_subfolder(image.get("subfolder", "")),
        type="output",
    )


def _queue_prompt_ids(payload: dict, field: str) -> tuple[str, ...]:
    rows = payload.get(field)
    if not isinstance(rows, list):
        raise ComfyProtocolError(f"{field} must be a list")
    prompt_ids: list[str] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            raise ComfyProtocolError(f"{field} row is malformed")
        prompt_id = row[1]
        if not isinstance(prompt_id, str):
            raise ComfyProtocolError("queue prompt ids must be strings")
        prompt_ids.append(prompt_id)
    return tuple(prompt_ids)
