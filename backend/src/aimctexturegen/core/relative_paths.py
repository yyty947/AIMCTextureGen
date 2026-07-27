"""Shared syntax validation for project-relative POSIX paths."""

from __future__ import annotations

_WINDOWS_DEVICE_STEMS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)
_WINDOWS_INVALID_CHARACTERS = frozenset('<>:"|?*')


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
    ):
        raise ValueError("invalid project-relative path")
    for segment in value.split("/"):
        if (
            segment in {"", ".", ".."}
            or any(character in _WINDOWS_INVALID_CHARACTERS for character in segment)
            or any(ord(character) < 32 for character in segment)
            or segment.endswith((" ", "."))
        ):
            raise ValueError("invalid project-relative path")
        device_stem = segment.split(".", maxsplit=1)[0].casefold()
        if device_stem in _WINDOWS_DEVICE_STEMS:
            raise ValueError("invalid Windows device path")
    return value
