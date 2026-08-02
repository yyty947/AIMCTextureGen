"""RED/GREEN tests for client error mapping and isolation."""

from __future__ import annotations

import subprocess
import sys

from aimctexturegen.comfy.client import ComfyClient
from aimctexturegen.comfy.errors import (
    ComfyDisconnectedError,
    ComfyError,
    ComfyExecutionError,
    ComfyProtocolError,
    ComfyQueueError,
    ComfyTimeoutError,
    ComfyUnsafeInputError,
    ComfyUnsafeOutputError,
)

from fakes.comfy_server import FakeComfyServer


def test_all_transport_errors_share_a_base() -> None:
    for error in (
        ComfyProtocolError,
        ComfyTimeoutError,
        ComfyQueueError,
        ComfyExecutionError,
        ComfyDisconnectedError,
        ComfyUnsafeInputError,
        ComfyUnsafeOutputError,
    ):
        assert issubclass(error, ComfyError)


def test_client_module_does_not_import_product_layers() -> None:
    probe = (
        "import sys; "
        "import aimctexturegen.comfy.client; "
        "banned = [m for m in ("
        "'aimctexturegen.jobs', "
        "'aimctexturegen.projects', "
        "'aimctexturegen.processing', "
        "'aimctexturegen.model_profiles.sdxl') "
        "if m in sys.modules]; "
        "assert not banned, banned"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_client_uses_generated_ids() -> None:
    with FakeComfyServer() as server:
        first = ComfyClient(server.base_url)
        second = ComfyClient(server.base_url)
        assert first.client_id != second.client_id
        assert first.client_id
