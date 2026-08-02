"""RED/GREEN tests for WebSocket progress and completion."""

from __future__ import annotations

from aimctexturegen.comfy.client import ComfyClient
from aimctexturegen.comfy.errors import (
    ComfyDisconnectedError,
    ComfyExecutionError,
    ComfyProtocolError,
    ComfyTimeoutError,
)

from fakes.comfy_server import FakeComfyServer

PROMPT_ID = "11111111-2222-3333-4444-555555555555"


def _executed(prompt_id: str = PROMPT_ID) -> dict:
    return {
        "type": "executed",
        "data": {"node": "9", "prompt_id": prompt_id, "output": {}},
    }


def _client(server: FakeComfyServer) -> ComfyClient:
    return ComfyClient(server.base_url, ws_base_url=server.ws_url)


def test_progress_is_ordered_and_filtered_by_prompt_id() -> None:
    script = [
        {"type": "progress", "data": {"value": 5, "max": 10, "prompt_id": "other"}},
        {"type": "progress", "data": {"value": 1, "max": 10, "prompt_id": PROMPT_ID}},
        {"type": "progress", "data": {"value": 7, "max": 10, "prompt_id": "other"}},
        {"type": "progress", "data": {"value": 9, "max": 10, "prompt_id": PROMPT_ID}},
        _executed(),
    ]
    history = {PROMPT_ID: {"outputs": {}}}
    with FakeComfyServer(ws_script=script, history=history) as server:
        client = _client(server)
        events: list[int] = []
        entry = client.wait_completion(
            PROMPT_ID,
            timeout=5.0,
            progress=lambda value, maximum: events.append(value),
        )
        assert entry == history[PROMPT_ID]
        assert events == [1, 9]


def test_execution_error_is_typed() -> None:
    script = [
        {
            "type": "execution_error",
            "data": {"prompt_id": PROMPT_ID, "exception_message": "OOM"},
        }
    ]
    with FakeComfyServer(ws_script=script) as server:
        client = _client(server)
        try:
            client.wait_completion(PROMPT_ID, timeout=5.0)
        except ComfyExecutionError as exc:
            assert "OOM" in str(exc)
        else:
            raise AssertionError("expected ComfyExecutionError")


def test_disconnect_before_completion_is_typed() -> None:
    with FakeComfyServer(ws_disconnect=True) as server:
        client = _client(server)
        try:
            client.wait_completion(PROMPT_ID, timeout=5.0)
        except ComfyDisconnectedError:
            pass
        else:
            raise AssertionError("expected ComfyDisconnectedError")


def test_timeout_is_typed() -> None:
    with FakeComfyServer(ws_hold=True) as server:
        client = _client(server)
        try:
            client.wait_completion(PROMPT_ID, timeout=0.3)
        except ComfyTimeoutError:
            pass
        else:
            raise AssertionError("expected ComfyTimeoutError")


def test_malformed_websocket_message_is_a_protocol_error() -> None:
    script = [{"type": "progress"}]  # missing data
    with FakeComfyServer(ws_script=script) as server:
        client = _client(server)
        try:
            client.wait_completion(PROMPT_ID, timeout=5.0)
        except ComfyProtocolError:
            pass
        else:
            raise AssertionError("expected ComfyProtocolError")
