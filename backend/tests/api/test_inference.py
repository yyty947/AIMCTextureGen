"""RED/GREEN tests for the managed inference setup API."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest

from aimctexturegen.api.inference import InferenceApplicationService
from aimctexturegen.comfy.errors import (
    InstallNotFoundError,
    InstallValidationError,
)
from aimctexturegen.comfy.install_state import InstallOperation
from aimctexturegen.main import AppServices, create_app
from aimctexturegen.inference.service import ManagedInferenceService


def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(
            app=app,
            raise_app_exceptions=False,
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


class FakeInferenceService(InferenceApplicationService):
    def __init__(self) -> None:
        self.current_operation = InstallOperation(
            operation_id=uuid4(),
            runtime_id="comfyui-windows-nvidia",
            profile_id="sdxl-mapchip-ipadapter",
            plan_digest="a" * 64,
            accepted_component_ids=(),
            state="planned",
            revision=1,
            created_at=datetime(2026, 8, 2, tzinfo=UTC),
            updated_at=datetime(2026, 8, 2, tzinfo=UTC),
        )
        self.install_error: Exception | None = None
        self.start_error: Exception | None = None
        self.starts = 0
        self.stops = 0

    def status(self) -> dict:
        return {
            "environment": {"supported": True},
            "runtime": {"state": "missing"},
            "profile": {"ready": False},
            "process": {"state": "stopped"},
        }

    def install_plan(self) -> dict:
        return {"plan_digest": "a" * 64, "can_install": True}

    def begin_install(self, accepted_component_ids: list[str]):
        if self.install_error is not None:
            raise self.install_error
        return self.current_operation

    def operation(self, operation_id: UUID):
        if operation_id != self.current_operation.operation_id:
            raise InstallNotFoundError("missing")
        return self.current_operation

    def cancel_operation(self, operation_id: UUID):
        return self.current_operation

    def start_comfyui(self) -> dict:
        self.starts += 1
        if self.start_error is not None:
            raise self.start_error
        return {"state": "ready", "pid": 1, "version": "0.29.2", "errors": ()}

    def stop_comfyui(self) -> dict:
        self.stops += 1
        return {"state": "stopped", "pid": None, "version": None, "errors": ()}

    def log_tail(self, max_bytes: int) -> str:
        return "tail"


def _app(service: FakeInferenceService):
    services = AppServices(
        workspace=object(),
        catalogs=object(),
        project_root=Path("C:/unused"),
        job_service=None,
        inference=service,
    )
    return create_app(services=services)


def test_status_and_install_plan_are_read_only() -> None:
    service = FakeInferenceService()
    app = _app(service)
    status_response = _request(app, "GET", "/api/system/inference")
    plan_response = _request(app, "GET", "/api/system/inference/install-plan")
    assert status_response.status_code == 200
    assert status_response.json()["process"]["state"] == "stopped"
    assert plan_response.status_code == 200
    assert plan_response.json()["can_install"] is True


def test_begin_install_returns_202_with_operation_id() -> None:
    service = FakeInferenceService()
    app = _app(service)
    response = _request(
        app,
        "POST",
        "/api/system/inference/installations",
        json={"accepted_component_ids": ["checkpoint"]},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["operation_id"] == str(service.current_operation.operation_id)
    assert body["state"] == "planned"


def test_stale_confirmation_is_a_stable_conflict() -> None:
    service = FakeInferenceService()
    service.install_error = InstallValidationError("stale consent")
    app = _app(service)
    response = _request(
        app,
        "POST",
        "/api/system/inference/installations",
        json={"accepted_component_ids": ["checkpoint"]},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "INSTALL_CONFIRMATION_STALE"


def test_unknown_installation_is_a_404() -> None:
    app = _app(FakeInferenceService())
    response = _request(
        app,
        "GET",
        f"/api/system/inference/installations/{uuid4()}",
    )
    assert response.status_code == 404
    assert response.json()["code"] == "INSTALL_NOT_FOUND"


def test_cancellation_returns_the_operation() -> None:
    service = FakeInferenceService()
    app = _app(service)
    response = _request(
        app,
        "POST",
        f"/api/system/inference/installations/{service.current_operation.operation_id}/cancel",
    )
    assert response.status_code == 200
    assert response.json()["operation_id"] == str(
        service.current_operation.operation_id
    )


def test_comfyui_start_and_stop_are_idempotent_endpoints() -> None:
    service = FakeInferenceService()
    app = _app(service)
    first = _request(app, "POST", "/api/system/inference/comfyui/start")
    second = _request(app, "POST", "/api/system/inference/comfyui/start")
    stop = _request(app, "POST", "/api/system/inference/comfyui/stop")
    assert first.status_code == 200
    assert second.status_code == 200
    assert stop.status_code == 200
    assert service.starts == 2
    assert service.stops == 1


def test_comfyui_start_failures_are_stable_errors() -> None:
    from aimctexturegen.comfy.errors import ManagerPortInUseError

    service = FakeInferenceService()
    service.start_error = ManagerPortInUseError("loopback port 8188 is already occupied")
    app = _app(service)
    response = _request(app, "POST", "/api/system/inference/comfyui/start")
    assert response.status_code == 409
    assert response.json()["code"] == "PORT_IN_USE"
    assert "8188" in response.json()["user_message"]
    assert any("不会终止" in action for action in response.json()["recommended_actions"])


def test_comfyui_readiness_timeout_has_cold_start_guidance() -> None:
    from aimctexturegen.comfy.errors import ManagerStartError

    service = FakeInferenceService()
    service.start_error = ManagerStartError(
        "managed child did not become ready in time"
    )
    app = _app(service)

    response = _request(app, "POST", "/api/system/inference/comfyui/start")

    assert response.status_code == 409
    assert response.json()["code"] == "COMFYUI_NOT_READY"
    assert any("冷启动" in action for action in response.json()["recommended_actions"])


def test_log_tail_is_bounded_and_has_no_path_input() -> None:
    app = _app(FakeInferenceService())
    response = _request(
        app,
        "GET",
        "/api/system/inference/comfyui/log?max_bytes=128",
    )
    assert response.status_code == 200
    assert response.json() == {"content": "tail"}
    invalid = _request(
        app,
        "GET",
        "/api/system/inference/comfyui/log?max_bytes=0",
    )
    assert invalid.status_code == 422


def test_default_service_reports_missing_state_without_creating_runtime(
    tmp_path: Path,
) -> None:
    from aimctexturegen.comfy.registry import ManifestRegistry

    root = Path(__file__).resolve().parents[3]
    registry = ManifestRegistry.load(root)
    runtime_root = tmp_path / "runtime"
    service = ManagedInferenceService(
        registry=registry,
        runtime_root=runtime_root,
    )
    status = service.status()
    assert status["runtime"]["state"] == "missing"
    assert status["profile"]["ready"] is False
    assert not runtime_root.exists()
    plan = service.install_plan()
    assert plan["profile_id"] == "sdxl-mapchip-ipadapter"
    assert not runtime_root.exists()


def test_default_service_recovery_marks_interrupted_operations(
    tmp_path: Path,
) -> None:
    from aimctexturegen.comfy.install_state import InstallOperationStore
    from aimctexturegen.comfy.registry import ManifestRegistry

    root = Path(__file__).resolve().parents[3]
    registry = ManifestRegistry.load(root)
    runtime_root = tmp_path / "runtime"
    store = InstallOperationStore(runtime_root / "state")
    operation = store.create(
        runtime_id="comfyui-windows-nvidia",
        profile_id="sdxl-mapchip-ipadapter",
        plan_digest="a" * 64,
        accepted_component_ids=(),
    )
    store.transition(operation, "downloading")

    ManagedInferenceService(registry=registry, runtime_root=runtime_root)

    recovered = store.get(operation.operation_id)
    assert recovered.state == "failed"
    assert recovered.error.code == "INSTALL_INTERRUPTED"


def test_managed_install_executes_created_operation_in_background(
    tmp_path: Path,
) -> None:
    from aimctexturegen.comfy.registry import ManifestRegistry

    class FakeDownloader:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def download(self, artifact, destination, **kwargs):
            self.calls.append(artifact.artifact_id)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"artifact")
            return None

    class FakeRuntimeInstaller:
        def __init__(self) -> None:
            self.calls = 0

        def status(self, runtime, root):
            from aimctexturegen.comfy.installer import RuntimeStatus

            return RuntimeStatus(state="missing")

        def install(self, runtime, archive, root):
            self.calls += 1

    class FakeProfileInstaller:
        def __init__(self) -> None:
            self.calls = 0

        def status(self, profile, root):
            from aimctexturegen.model_profiles.models import ProfileStatus

            return ProfileStatus(
                profile_id=profile.profile_id,
                profile_version=profile.profile_version,
                support_state=profile.support_state,
                components=tuple(),
                ready=False,
            )

        def install(self, profile, root, **kwargs):
            self.calls += 1

        def write_extra_model_paths(self, profile, root):
            path = root / "state" / "extra_model_paths.yaml"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("models:\n", encoding="utf-8")
            return path

    root = Path(__file__).resolve().parents[3]
    registry = ManifestRegistry.load(root)
    downloader = FakeDownloader()
    runtime_installer = FakeRuntimeInstaller()
    profile_installer = FakeProfileInstaller()
    service = ManagedInferenceService(
        registry=registry,
        runtime_root=tmp_path / "runtime",
        artifact_downloader=downloader,
        runtime_installer=runtime_installer,
        profile_installer=profile_installer,
    )
    plan = service.install_plan()
    operation = service.begin_install(
        [component["artifact_id"] for component in plan["components"]]
    )

    for _ in range(100):
        current = service.operation(operation.operation_id)
        if current.state == "completed":
            break
        time.sleep(0.01)

    current = service.operation(operation.operation_id)
    assert current.state == "completed"
    assert runtime_installer.calls == 1
    assert profile_installer.calls == 1
    assert downloader.calls == ["comfyui-windows-portable-nvidia-v0.29.2"]
