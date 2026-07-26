from __future__ import annotations

import hashlib
import os
from pathlib import Path

import PIL
from PIL import Image

from .grid_snap import snap_to_grid
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
    writer(temporary)
    os.replace(temporary, directory / name)


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
            unique_colors=len(set(final.getdata())),
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
