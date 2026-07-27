"""Deterministic candidate processing and artifact publication."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import PIL
from PIL import Image

from .grid_snap import snap_to_grid
from .errors import ProcessingError
from .models import (
    ALGORITHM_VERSION,
    GridSnapInfo,
    ImageRef,
    PaletteInfo,
    PreviewRefs,
    ProcessingReport,
    ProcessorInfo,
    SeamScoreInfo,
    dump_report_json,
)
from .palette import limit_palette
from .previews import nearest_neighbor_preview, tile_3x3
from .seam import seam_scores
from .validation import load_rgb_canvas


def _replace_into(directory: Path, name: str, writer) -> None:
    temporary = directory / f"{name}.tmp"
    pending_error = False
    try:
        writer(temporary)
        os.replace(temporary, directory / name)
    except BaseException:
        pending_error = True
        raise
    finally:
        try:
            _remove_temporary_file(directory, temporary)
        except OSError:
            if not pending_error:
                raise


def _remove_temporary_file(directory: Path, temporary: Path) -> None:
    """Remove only an unchanged regular temporary file below ``directory``."""
    if temporary.parent != directory:
        return
    try:
        metadata = temporary.lstat()
    except FileNotFoundError:
        return
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    if not stat.S_ISREG(metadata.st_mode) or file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return
    try:
        temporary.unlink()
    except FileNotFoundError:
        return


def _write_png(image: Image.Image, directory: Path, name: str) -> ImageRef:
    _replace_into(directory, name, lambda path: image.save(path, format="PNG", optimize=False))
    data = (directory / name).read_bytes()
    return ImageRef(
        path=name,
        sha256=hashlib.sha256(data).hexdigest(),
        width=image.width,
        height=image.height,
    )


def process_candidate(
    source: Path,
    output_dir: Path,
    *,
    stem: str,
    resolution: int,
    palette_limit: int | None = None,
) -> ProcessingReport:
    """Run the deterministic Phase 2 pipeline for one raw candidate."""
    if resolution not in (16, 32, 64):
        raise ProcessingError("INVALID_RESOLUTION", "目标分辨率必须是 16、32 或 64")
    if (
        not stem
        or stem in {".", ".."}
        or "/" in stem
        or "\\" in stem
        or "\x00" in stem
    ):
        raise ProcessingError("INVALID_OUTPUT_STEM", "候选文件名无效")
    canvas, input_mode = load_rgb_canvas(source, resolution)
    snapped = snap_to_grid(canvas, resolution)
    final = limit_palette(snapped, palette_limit) if palette_limit is not None else snapped

    output_dir.mkdir(parents=True, exist_ok=True)
    final_ref = _write_png(final, output_dir, f"{stem}.png")
    nn_ref = _write_png(nearest_neighbor_preview(final), output_dir, f"{stem}-nn.png")
    tile_ref = _write_png(tile_3x3(final), output_dir, f"{stem}-tile.png")

    horizontal, vertical, average = seam_scores(final)
    report = ProcessingReport(
        schema_version=1,
        processor=ProcessorInfo(
            algorithm_version=ALGORITHM_VERSION, pillow_version=PIL.__version__
        ),
        input=ImageRef(
            path=source.name,
            sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            width=canvas.width,
            height=canvas.height,
        ),
        input_mode=input_mode,
        output=final_ref,
        resolution=resolution,
        grid_snap=GridSnapInfo(
            method="per-channel-median-low", cell_pixels=canvas.width // resolution
        ),
        palette=PaletteInfo(
            unique_colors=len(set(final.get_flattened_data())),
            limit=palette_limit,
            method="median-cut" if palette_limit is not None else None,
        ),
        seam_score=SeamScoreInfo(horizontal=horizontal, vertical=vertical, average=average),
        previews=PreviewRefs(nearest_neighbor=nn_ref, tile_3x3=tile_ref),
    )
    _replace_into(
        output_dir, f"{stem}-report.json", lambda path: path.write_bytes(dump_report_json(report))
    )
    return report
