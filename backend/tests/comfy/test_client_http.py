"""RED/GREEN tests for ComfyClient HTTP operations."""

from __future__ import annotations

import pytest

from aimctexturegen.comfy.client import ComfyClient, ComfyOutputImage
from aimctexturegen.comfy.errors import (
    ComfyProtocolError,
    ComfyQueueError,
    ComfyUnsafeInputError,
    ComfyUnsafeOutputError,
)

from fakes.comfy_server import FakeComfyServer


def _client(server: FakeComfyServer) -> ComfyClient:
    return ComfyClient(server.base_url)


def test_system_stats_and_object_info_are_returned() -> None:
    with FakeComfyServer() as server:
        client = _client(server)
        stats = client.system_stats()
        assert stats["system"]["comfyui_version"] == "0.29.2"
        assert "CheckpointLoaderSimple" in client.object_info()


def test_upload_sends_bytes_and_sanitizes_the_filename() -> None:
    with FakeComfyServer() as server:
        client = _client(server)
        result = client.upload_image(b"png", "C:\\some\\dir\\texture.png")
        assert result["name"] == "texture.png"
        assert server.last_upload_name == "texture.png"


def test_upload_rejects_unsafe_or_oversized_inputs() -> None:
    with FakeComfyServer() as server:
        client = _client(server)
        with pytest.raises(ComfyUnsafeInputError):
            client.upload_image(b"x", "")
        with pytest.raises(ComfyUnsafeInputError):
            client.upload_image(b"x", "..\\evil.png")
        with pytest.raises(ComfyUnsafeInputError):
            client.upload_image(b"x", "/etc/passwd.png")
        with pytest.raises(ComfyUnsafeInputError):
            client.upload_image(b"x" * (50 * 1024 * 1024 + 1), "big.png")


def test_submit_prompt_deep_copies_and_returns_prompt_id() -> None:
    with FakeComfyServer() as server:
        client = _client(server)
        workflow = {"1": {"class_type": "KSampler", "inputs": {"seed": 1}}}
        prompt_id = client.submit_prompt(workflow)
        workflow["1"]["inputs"]["seed"] = 999
        workflow["evil"] = True
        assert prompt_id == "11111111-2222-3333-4444-555555555555"
        assert server.last_prompt["prompt"]["1"]["inputs"]["seed"] == 1
        assert "evil" not in server.last_prompt["prompt"]
        assert server.last_prompt["client_id"] == client.client_id


def test_queue_error_and_malformed_responses_are_typed() -> None:
    with FakeComfyServer(prompt_behavior="queue-error") as server:
        with pytest.raises(ComfyQueueError):
            _client(server).submit_prompt({"1": {}})
    with FakeComfyServer(prompt_behavior="malformed") as server:
        with pytest.raises(ComfyProtocolError):
            _client(server).submit_prompt({"1": {}})


def test_history_retrieval_and_output_are_bounded_to_declared_names() -> None:
    history = {
        "p1": {
            "outputs": {
                "9": {
                    "images": [
                        {
                            "filename": "result.png",
                            "subfolder": "",
                            "type": "output",
                        }
                    ]
                }
            }
        }
    }
    with FakeComfyServer(history=history, view_bytes=b"png-data") as server:
        client = _client(server)
        entry = client.get_history("p1")
        assert entry["outputs"]["9"]["images"][0]["filename"] == "result.png"
        output = client.get_output(entry, "result.png")
        assert output == b"png-data"
        with pytest.raises(ComfyUnsafeOutputError):
            client.get_output(entry, "../other.png")
        with pytest.raises(ComfyUnsafeOutputError):
            client.get_output(entry, "not-declared.png")


def test_declared_output_images_preserve_selected_node_order() -> None:
    history = {
        "p1": {
            "outputs": {
                "19": {
                    "images": [
                        {
                            "filename": "a_00001_.png",
                            "subfolder": "",
                            "type": "output",
                        },
                        {
                            "filename": "a_00002_.png",
                            "subfolder": "",
                            "type": "output",
                        },
                    ]
                },
                "20": {
                    "images": [
                        {
                            "filename": "other_00001_.png",
                            "subfolder": "",
                            "type": "output",
                        }
                    ]
                },
            }
        }
    }
    with FakeComfyServer(history=history) as server:
        client = _client(server)
        entry = client.get_history("p1")
        images = client.declared_output_images(entry, output_node_id="19")
        assert images == (
            ComfyOutputImage("a_00001_.png", "", "output"),
            ComfyOutputImage("a_00002_.png", "", "output"),
        )


@pytest.mark.parametrize(
    ("history", "message"),
    [
        ({"outputs": {}}, "missing"),
        (
            {
                "outputs": {
                    "19": {
                        "images": [
                            {
                                "filename": "dup.png",
                                "subfolder": "",
                                "type": "output",
                            },
                            {
                                "filename": "dup.png",
                                "subfolder": "",
                                "type": "output",
                            },
                        ]
                    }
                }
            },
            "duplicate",
        ),
        (
            {
                "outputs": {
                    "19": {
                        "images": [
                            {
                                "filename": "../escape.png",
                                "subfolder": "",
                                "type": "output",
                            }
                        ]
                    }
                }
            },
            "unsafe",
        ),
        (
            {
                "outputs": {
                    "19": {
                        "images": [
                            {
                                "filename": "ok.png",
                                "subfolder": "../escape",
                                "type": "output",
                            }
                        ]
                    }
                }
            },
            "unsafe",
        ),
        (
            {
                "outputs": {
                    "19": {
                        "images": [
                            {
                                "filename": "ok.png",
                                "subfolder": "",
                                "type": "temp",
                            }
                        ]
                    }
                }
            },
            "unsafe",
        ),
        (
            {
                "outputs": {
                    "19": {
                        "images": {
                            "filename": "wrong.png",
                            "subfolder": "",
                            "type": "output",
                        }
                    }
                }
            },
            "images",
        ),
    ],
)
def test_declared_output_images_reject_contract_violations(
    history: dict,
    message: str,
) -> None:
    with FakeComfyServer(history={"p1": history}) as server:
        client = _client(server)
        with pytest.raises(ComfyUnsafeOutputError, match=message):
            client.declared_output_images(
                client.get_history("p1"),
                output_node_id="19",
            )


def test_declared_output_download_uses_typed_descriptor_and_is_bounded() -> None:
    history = {
        "p1": {
            "outputs": {
                "19": {
                    "images": [
                        {
                            "filename": "a_00001_.png",
                            "subfolder": "",
                            "type": "output",
                        }
                    ]
                }
            }
        }
    }
    big_bytes = b"x" * (8 * 1024 * 1024 + 1)
    with FakeComfyServer(
        history=history,
        view_bytes_by_name={"a_00001_.png": b"png-data", "big.png": big_bytes},
    ) as server:
        client = _client(server)
        image = client.declared_output_images(
            client.get_history("p1"),
            output_node_id="19",
        )[0]
        assert client.get_output_image(image) == b"png-data"
        with pytest.raises(ComfyProtocolError, match="limit"):
            client.get_output_image(
                ComfyOutputImage("big.png", "", "output"),
            )


def test_queue_and_targeted_interrupt_are_prompt_scoped() -> None:
    prompt_id = "11111111-2222-3333-4444-555555555555"
    with FakeComfyServer(
        queue_running=[[0, prompt_id, {}, {}]],
        queue_pending=[[1, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", {}, {}]],
    ) as server:
        client = _client(server)
        snapshot = client.queue_snapshot()
        assert snapshot.running_prompt_ids == (prompt_id,)
        assert snapshot.pending_prompt_ids == (
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
        client.interrupt(prompt_id)
        assert server.last_interrupt_prompt_id == prompt_id


def test_queue_snapshot_rejects_non_string_prompt_ids() -> None:
    with FakeComfyServer(queue_running=[[0, 123, {}, {}]]) as server:
        with pytest.raises(ComfyProtocolError, match="prompt"):
            _client(server).queue_snapshot()


def test_interrupt_is_sent() -> None:
    with FakeComfyServer() as server:
        _client(server).interrupt()


def test_loopback_base_url_is_enforced() -> None:
    with pytest.raises(ValueError):
        ComfyClient("http://example.com:8188")


def test_missing_history_and_malformed_history_are_protocol_errors() -> None:
    with FakeComfyServer(history_behavior="malformed") as server:
        with pytest.raises(ComfyProtocolError):
            _client(server).get_history("p1")


def test_client_close_cleans_up_http() -> None:
    with FakeComfyServer() as server:
        client = _client(server)
        client.close()
        assert client._http.is_closed
