"""Shared syntax validation for project-relative POSIX paths."""

from __future__ import annotations

import re


_WINDOWS_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


def validate_project_relative_path(value: str) -> str:
    """Return a canonical relative path or reject unsafe path syntax."""

    if not isinstance(value, str):
        raise ValueError("project-relative path must be a string")
    if (
        not value
        or value.startswith("/")
        or value.endswith("/")
        or "\\" in value
        or "\x00" in value
        or _WINDOWS_DRIVE_PREFIX.match(value)
    ):
        raise ValueError("invalid project-relative path")
    if any(segment in {"", ".", ".."} for segment in value.split("/")):
        raise ValueError("invalid project-relative path")
    return value
