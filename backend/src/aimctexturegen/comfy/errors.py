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
