from __future__ import annotations

import math

from PIL import Image

from .models import SCORE_DECIMALS

_NORMALIZER = math.sqrt(3.0) * 255.0


def _edge_score(first, second) -> float:
    total = 0.0
    for (r1, g1, b1), (r2, g2, b2) in zip(first, second, strict=True):
        total += math.sqrt((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2)
    return round(total / (len(first) * _NORMALIZER), SCORE_DECIMALS)


def seam_scores(texture: Image.Image) -> tuple[float, float, float]:
    """Return (horizontal, vertical, average) wrap-seam scores in [0, 1]."""
    pixels = texture.load()
    width, height = texture.size
    left = [pixels[0, y] for y in range(height)]
    right = [pixels[width - 1, y] for y in range(height)]
    top = [pixels[x, 0] for x in range(width)]
    bottom = [pixels[x, height - 1] for x in range(width)]
    horizontal = _edge_score(left, right)
    vertical = _edge_score(top, bottom)
    average = round((horizontal + vertical) / 2.0, SCORE_DECIMALS)
    return horizontal, vertical, average
