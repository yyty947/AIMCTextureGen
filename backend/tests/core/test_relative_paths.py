import pytest

from aimctexturegen.core.relative_paths import validate_project_relative_path


@pytest.mark.parametrize(
    "value",
    [
        "pack.mcmeta",
        "assets/minecraft/textures/block/stone.png",
        "uploads/structure-references/参考.png",
        "namespace:variant/file.png",
    ],
)
def test_valid_project_relative_paths_are_returned_unchanged(value: str) -> None:
    assert validate_project_relative_path(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "/absolute/path.png",
        "trailing/slash/",
        "double//slash.png",
        "./relative.png",
        "assets/../escape.png",
        "assets/./stone.png",
        r"assets\minecraft\stone.png",
        "C:/drive/path.png",
        "C:drive-relative.png",
        r"\\server\share\file.png",
        r"\\?\C:\device\file.png",
        "//server/share/file.png",
        "//?/C:/device/file.png",
        "assets/minecraft/\x00stone.png",
    ],
)
def test_unsafe_or_noncanonical_project_relative_paths_are_rejected(
    value: str,
) -> None:
    with pytest.raises(ValueError):
        validate_project_relative_path(value)
