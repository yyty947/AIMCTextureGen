import os
import stat
from pathlib import Path, PurePosixPath
from typing import Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict

from aimctexturegen.catalog.models import CatalogEntry, CatalogProfile


CoverageStatus = Literal["covered", "missing"]


class CoverageValidationError(ValueError):
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        super().__init__(f"目录材质 PNG 无法解码: {relative_path}")


class CoverageItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    semantic_id: str
    display_name: str
    relative_path: str
    mvp_eligible: bool
    status: CoverageStatus


class CoverageReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    catalog_id: str
    catalog_status: Literal["development_fixture", "production"]
    covered_count: int
    missing_count: int
    unknown_paths: tuple[str, ...]
    items: tuple[CoverageItem, ...]


def classify_coverage(pack_root: Path, profile: CatalogProfile) -> CoverageReport:
    files = _enumerate_files(pack_root)
    catalog_paths = frozenset(entry.relative_path for entry in profile.entries)
    items = tuple(
        _coverage_item(entry, files.get(entry.relative_path))
        for entry in profile.entries
    )
    unknown_paths = tuple(
        sorted(
            relative_path
            for relative_path, path in files.items()
            if relative_path not in catalog_paths
            and _is_unknown_reference_candidate(relative_path, path)
        )
    )

    return CoverageReport(
        catalog_id=profile.catalog_id,
        catalog_status=profile.status,
        covered_count=sum(item.status == "covered" for item in items),
        missing_count=sum(item.status == "missing" for item in items),
        unknown_paths=unknown_paths,
        items=items,
    )


def _enumerate_files(pack_root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for current_root, directory_names, file_names in os.walk(
        pack_root,
        topdown=True,
        followlinks=False,
    ):
        current = Path(current_root)
        directory_names[:] = [
            name
            for name in directory_names
            if not _is_directory_reparse_point(current / name)
        ]
        for name in file_names:
            path = current / name
            files[path.relative_to(pack_root).as_posix()] = path
    return files


def _is_directory_reparse_point(path: Path) -> bool:
    status = os.lstat(path)
    return (
        path.is_symlink()
        or path.is_junction()
        or bool(
            getattr(status, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )
    )


def _coverage_item(entry: CatalogEntry, path: Path | None) -> CoverageItem:
    status: CoverageStatus = "missing"
    if path is not None:
        if not _is_decodable_png(path):
            raise CoverageValidationError(entry.relative_path)
        status = "covered"
    return CoverageItem(
        semantic_id=entry.semantic_id,
        display_name=entry.display_name,
        relative_path=entry.relative_path,
        mvp_eligible=entry.mvp_eligible,
        status=status,
    )


def _is_unknown_reference_candidate(relative_path: str, path: Path) -> bool:
    path_parts = PurePosixPath(relative_path).parts
    return (
        len(path_parts) >= 4
        and path_parts[0] == "assets"
        and path_parts[2] == "textures"
        and _is_decodable_square_png(path)
    )


def _is_decodable_png(path: Path) -> bool:
    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                return False
            image.verify()
    except (OSError, SyntaxError):
        return False
    return True


def _is_decodable_square_png(path: Path) -> bool:
    if not _is_decodable_png(path):
        return False
    try:
        with Image.open(path) as image:
            return image.size[0] == image.size[1]
    except (OSError, SyntaxError):
        return False
