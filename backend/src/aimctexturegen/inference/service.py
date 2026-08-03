"""Default managed-inference application service."""

from __future__ import annotations

import threading
from pathlib import Path
from uuid import UUID

from aimctexturegen.comfy.client import ComfyClient
from aimctexturegen.comfy.downloads import ArtifactDownloader, DownloadPolicy
from aimctexturegen.comfy.environment import EnvironmentInspector
from aimctexturegen.comfy.errors import (
    DownloadCanceled,
    InstallNotFoundError,
    InstallStateError,
)
from aimctexturegen.comfy.install_state import (
    InstallErrorRecord,
    InstallOperation,
    InstallOperationStore,
)
from aimctexturegen.comfy.installer import (
    DEFAULT_SETUP_PROFILE_KEY,
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
    """Managed setup status plus consent-bound background installation."""

    def __init__(
        self,
        *,
        registry: ManifestRegistry,
        runtime_root: Path,
        port: int = 8188,
        artifact_downloader: ArtifactDownloader | None = None,
        runtime_installer: RuntimeInstaller | None = None,
        profile_installer: ProfileInstaller | None = None,
    ) -> None:
        self._registry = registry
        self._root = Path(runtime_root)
        self._port = port
        self._inspector = EnvironmentInspector()
        self._installer = Installer(registry, self._inspector)
        self._store = InstallOperationStore(self._root / "state")
        self._runtime = registry.runtime("comfyui-windows-nvidia")
        self._profile = registry.profile(*DEFAULT_SETUP_PROFILE_KEY)
        self._artifact_downloader = artifact_downloader or ArtifactDownloader()
        self._runtime_installer = runtime_installer or RuntimeInstaller()
        self._profile_installer = profile_installer or ProfileInstaller()
        self._install_threads: dict[UUID, threading.Thread] = {}
        self._cancel_events: dict[UUID, threading.Event] = {}
        self._lifecycle_lock = threading.Lock()
        self._closing = False
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
            DEFAULT_SETUP_PROFILE_KEY[0],
            DEFAULT_SETUP_PROFILE_KEY[1],
            self._root,
        ).model_dump(mode="json")

    def begin_install(
        self,
        accepted_component_ids: list[str],
    ) -> InstallOperation:
        with self._lifecycle_lock:
            if self._closing:
                raise InstallStateError("inference service is shutting down")
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
                DEFAULT_SETUP_PROFILE_KEY[0],
                DEFAULT_SETUP_PROFILE_KEY[1],
                self._root,
            )
            consent = self._installer.consent(plan, accepted_component_ids)
            operation = self._installer.begin_install(consent, self._root)
            cancel_event = threading.Event()
            self._cancel_events[operation.operation_id] = cancel_event
            worker = threading.Thread(
                target=self._execute_install,
                args=(operation.operation_id, cancel_event),
                name=f"aimc-install-{operation.operation_id}",
                daemon=True,
            )
            self._install_threads[operation.operation_id] = worker
            worker.start()
        return operation

    def operation(self, operation_id: UUID) -> InstallOperation:
        operation = self._store.get(operation_id)
        if operation is None:
            raise InstallNotFoundError("install operation does not exist")
        return operation

    def cancel_operation(self, operation_id: UUID) -> InstallOperation:
        with self._lifecycle_lock:
            operation = self.operation(operation_id)
            if operation.state in {
                "planned",
                "downloading",
                "extracting",
                "installing",
            }:
                event = self._cancel_events.get(operation_id)
                if event is not None:
                    event.set()
                return self._store.transition(operation, "canceled")
            return operation

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

    def shutdown(self) -> None:
        """Stop accepting installs and let active workers observe cancellation."""

        with self._lifecycle_lock:
            self._closing = True
            for event in self._cancel_events.values():
                event.set()
            workers = tuple(self._install_threads.values())
        for worker in workers:
            worker.join(timeout=2.0)
        try:
            self._manager.stop()
        except Exception:
            # Process identity checks deliberately fail closed; leave the
            # record for the next startup recovery rather than killing an
            # unrelated process during application shutdown.
            pass

    def _execute_install(
        self,
        operation_id: UUID,
        cancel_event: threading.Event,
    ) -> None:
        try:
            operation = self._transition_if_active(operation_id, "downloading")
            if operation is None:
                return
            runtime = self._runtime
            profile = self._profile
            runtime_status = self._runtime_installer.status(runtime, self._root)
            if runtime_status.state != "ready":
                archive = self._root / runtime.archive.destination
                self._artifact_downloader.download(
                    runtime.archive,
                    archive,
                    policy=DownloadPolicy(
                        allowed_hosts=runtime.archive.allowed_hosts
                    ),
                    cancel=cancel_event.is_set,
                )
                if cancel_event.is_set():
                    self._cancel_if_active(operation_id)
                    return
                operation = self._transition_if_active(operation_id, "extracting")
                if operation is None:
                    return
                self._runtime_installer.install(runtime, archive, self._root)
            if cancel_event.is_set():
                self._cancel_if_active(operation_id)
                return
            operation = self._transition_if_active(operation_id, "installing")
            if operation is None:
                return
            self._profile_installer.install(
                profile,
                self._root,
                cancel=cancel_event.is_set,
            )
            self._profile_installer.write_extra_model_paths(profile, self._root)
            if cancel_event.is_set():
                self._cancel_if_active(operation_id)
                return
            self._transition_if_active(operation_id, "completed")
        except DownloadCanceled:
            self._cancel_if_active(operation_id)
        except Exception as error:
            self._fail_operation(operation_id, error)
        finally:
            with self._lifecycle_lock:
                self._install_threads.pop(operation_id, None)
                self._cancel_events.pop(operation_id, None)

    def _cancel_if_active(self, operation_id: UUID) -> None:
        with self._lifecycle_lock:
            operation = self._store.get(operation_id)
            if operation is not None and operation.state not in {
                "completed",
                "failed",
                "canceled",
            }:
                self._store.transition(operation, "canceled")

    def _fail_operation(self, operation_id: UUID, error: Exception) -> None:
        with self._lifecycle_lock:
            operation = self._store.get(operation_id)
            if operation is None or operation.state in {
                "completed",
                "failed",
                "canceled",
            }:
                return
            code = error.__class__.__name__.upper()
            self._store.transition(
                operation,
                "failed",
                error=InstallErrorRecord(
                    code=code,
                    message=str(error)[:400] or "installation failed",
                ),
            )

    def _transition_if_active(
        self,
        operation_id: UUID,
        state: str,
    ) -> InstallOperation | None:
        with self._lifecycle_lock:
            operation = self._store.get(operation_id)
            if operation is None or operation.state in {
                "completed",
                "failed",
                "canceled",
            }:
                return None
            return self._store.transition(operation, state)  # type: ignore[arg-type]
