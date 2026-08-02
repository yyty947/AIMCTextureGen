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
