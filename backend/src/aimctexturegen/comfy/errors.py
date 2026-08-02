"""Stable domain errors for managed inference manifests and setup."""

from __future__ import annotations


class ManifestError(ValueError):
    """Base error for strict manifest handling."""


class ManifestNotFoundError(ManifestError):
    """A required manifest file or directory is missing."""


class ManifestValidationError(ManifestError):
    """A manifest file failed strict validation."""


class InstallError(Exception):
    """Base error for managed inference installation."""


class InstallValidationError(InstallError):
    """An install request failed consent or validation checks."""


class InstallBlockedError(InstallError):
    """The host or environment blocks installation."""


class InstallStateError(InstallError):
    """An install operation record is in an invalid state."""


class InstallNotFoundError(InstallError):
    """An install operation does not exist."""


class DownloadError(Exception):
    """Base error for managed artifact downloads."""


class DownloadCanceled(DownloadError):
    """The download was canceled by the caller."""


class DownloadHashMismatch(DownloadError):
    """Downloaded bytes do not match the expected SHA-256."""


class DownloadSizeMismatch(DownloadError):
    """Downloaded bytes do not match the expected size."""


class DownloadProtocolError(DownloadError):
    """HTTP, redirect, range or timeout contract was violated."""


class DownloadUnsafePathError(DownloadError):
    """A destination, partial or sidecar path is unsafe."""


class ArchiveError(Exception):
    """Base error for managed archive handling."""


class ArchiveUnsafeError(ArchiveError):
    """An archive member or extracted tree is unsafe."""


class ArchiveExtractError(ArchiveError):
    """The archive could not be listed, extracted or audited."""


class RuntimeInstallError(Exception):
    """Base error for managed runtime publication."""


class RuntimeInstallValidationError(RuntimeInstallError):
    """The archive or runtime does not satisfy the manifest contract."""


class RuntimePublicationError(RuntimeInstallError):
    """The verified runtime tree could not be published atomically."""


class ProfileInstallError(Exception):
    """Base error for model-profile installation."""


class ProfileUnsafeArtifactError(ProfileInstallError):
    """A downloaded model or custom-node artifact is unsafe or invalid."""


class ProcessError(Exception):
    """Base error for owned child-process lifecycle."""


class ProcessIdentityError(ProcessError):
    """The live process no longer matches the owned record identity."""


class PortInUseError(ProcessError):
    """The configured loopback port is already occupied."""


class ProcessStartError(ProcessError):
    """The child process could not be started."""


class ProcessStopError(ProcessError):
    """The owned child process could not be stopped."""


class ManagerError(Exception):
    """Base error for ComfyUI manager orchestration."""


class ManagerStartError(ManagerError):
    """The managed runtime could not be started or verified."""


class ReadinessError(ManagerError):
    """The managed runtime did not become ready."""


class ComfyError(Exception):
    """Base error for ComfyUI protocol transport."""


class ComfyProtocolError(ComfyError):
    """A response or WebSocket message violated the protocol contract."""


class ComfyTimeoutError(ComfyError):
    """An operation exceeded its finite deadline."""


class ComfyQueueError(ComfyError):
    """ComfyUI rejected the submitted prompt."""


class ComfyExecutionError(ComfyError):
    """ComfyUI reported an execution error for the prompt."""


class ComfyDisconnectedError(ComfyError):
    """The WebSocket or transport disconnected unexpectedly."""


class ComfyUnsafeInputError(ComfyError):
    """An upload or input violates the safe boundary."""


class ComfyUnsafeOutputError(ComfyError):
    """An output name was not declared by the prompt history."""


class WorkflowError(Exception):
    """Base error for fixed workflow templates and bindings."""


class WorkflowBindingError(WorkflowError):
    """A workflow template or semantic binding violated its contract."""


class ProfileBindingError(Exception):
    """A durable job profile binding could not be constructed."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)
