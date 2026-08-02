"""Default managed-inference application service."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from aimctexturegen.comfy.client import ComfyClient
from aimctexturegen.comfy.environment import EnvironmentInspector
from aimctexturegen.comfy.errors import (
    InstallNotFoundError,
    InstallStateError,
)
from aimctexturegen.comfy.install_state import (
    InstallOperation,
    InstallOperationStore,
)
from aimctexturegen.comfy.installer import (
    Installer,
    ProfileInstaller,
    RuntimeInstaller,
)
from aimctexturegen.comfy.manager import ComfyUIManager, ReadinessProbe
from aimctexturegen.comfy.process import ProcessLauncher
from aimctexturegen.comfy.registry import ManifestRegistry


class _ClientProbe(ReadinessProbe):
    def __init__(self, port: int) -> None:
        self._client = ComfyClient(f"http://127.0.0.1:{port}")

    def system_stats(self) -> dict:
        return self._client.system_stats()

    def object_info(self) -> dict:
        return self._client.object_info()


class ManagedInferenceService:
    """Read-only setup status plus consent-bound operation creation."""

    def __init__(
        self,
        *,
        registry: ManifestRegistry,
        runtime_root: Path,
        port: int = 8188,
    ) -> None:
        self._registry = registry
        self._root = Path(runtime_root)
        self._port = port
        self._inspector = EnvironmentInspector()
        self._installer = Installer(registry, self._inspector)
        self._store = InstallOperationStore(self._root / "state")
        self._runtime = registry.runtime("comfyui-windows-nvidia")
        self._profile = registry.profile("sdxl-mapchip-ipadapter")
        self._runtime_installer = RuntimeInstaller()
        self._profile_installer = ProfileInstaller()
        self._manager = ComfyUIManager(
            runtime_root=self._root,
            runtime=self._runtime,
            profile=self._profile,
            launcher=ProcessLauncher(),
            probe=_ClientProbe(port),
            port=port,
        )
        self._store.recover_interrupted()

    def status(self) -> dict:
        return {
            "environment": self._inspector.inspect(self._root).model_dump(
                mode="json"
            ),
            "runtime": self._runtime_installer.status(
                self._runtime,
                self._root,
            ).model_dump(mode="json"),
            "profile": self._profile_installer.status(
                self._profile,
                self._root,
            ).model_dump(mode="json"),
            "process": self._manager.status().model_dump(mode="json"),
        }

    def install_plan(self) -> dict:
        return self._installer.inspect(
            "comfyui-windows-nvidia",
            "sdxl-mapchip-ipadapter",
            self._root,
        ).model_dump(mode="json")

    def begin_install(
        self,
        accepted_component_ids: list[str],
    ) -> InstallOperation:
        active = [
            operation
            for operation in self._store.list_operations()
            if operation.state
            in {"planned", "downloading", "extracting", "installing"}
        ]
        if active:
            raise InstallStateError(
                "only one active install operation is allowed"
            )
        plan = self._installer.inspect(
            "comfyui-windows-nvidia",
            "sdxl-mapchip-ipadapter",
            self._root,
        )
        consent = self._installer.consent(plan, accepted_component_ids)
        return self._installer.begin_install(consent, self._root)

    def operation(self, operation_id: UUID) -> InstallOperation:
        operation = self._store.get(operation_id)
        if operation is None:
            raise InstallNotFoundError("install operation does not exist")
        return operation

    def cancel_operation(self, operation_id: UUID) -> InstallOperation:
        operation = self.operation(operation_id)
        return self._store.transition(operation, "canceled")

    def start_comfyui(self) -> dict:
        return self._manager.start().model_dump(mode="json")

    def stop_comfyui(self) -> dict:
        return self._manager.stop().model_dump(mode="json")

    def log_tail(self, max_bytes: int) -> str:
        bounded = max(1, min(int(max_bytes), 64 * 1024))
        log_path = self._root / "logs" / "comfyui.log"
        try:
            raw = log_path.read_bytes()
        except OSError:
            return ""
        return raw[-bounded:].decode("utf-8", errors="replace")
