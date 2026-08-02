"""Stable domain errors for managed inference manifests and setup."""

from __future__ import annotations


class ManifestError(ValueError):
    """Base error for strict manifest handling."""


class ManifestNotFoundError(ManifestError):
    """A required manifest file or directory is missing."""


class ManifestValidationError(ManifestError):
    """A manifest file failed strict validation."""
