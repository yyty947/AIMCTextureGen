"""Deterministic median-cut palette limiting.

Every ordering and tie-break is fixed so identical inputs always yield
identical outputs; see the phase plan for the rule list.
"""

from __future__ import annotations

from collections import Counter

from PIL import Image

from .errors import ProcessingError

_CHANNELS = (0, 1, 2)

_Box = list[tuple[tuple[int, int, int], int]]


def _pixel_count(box: _Box) -> int:
    return sum(count for _color, count in box)


def _split_target_index(boxes: list[_Box]) -> int | None:
    best_index: int | None = None
    best_count = 0
    for index, box in enumerate(boxes):
        if len(box) < 2:
            continue
        count = _pixel_count(box)
        if count > best_count:
            best_count = count
            best_index = index
    return best_index


def _split_channel(box: _Box) -> int:
    best_channel = 0
    best_range = -1
    for channel in _CHANNELS:
        values = [color[channel] for color, _count in box]
        value_range = max(values) - min(values)
        if value_range > best_range:
            best_range = value_range
            best_channel = channel
    return best_channel


def _split_box(box: _Box) -> tuple[_Box, _Box]:
    channel = _split_channel(box)
    ordered = sorted(box, key=lambda item: (item[0][channel], item[0]))
    total = _pixel_count(box)
    accumulated = 0
    split_index = len(ordered) - 1
    for index, (_color, count) in enumerate(ordered):
        accumulated += count
        if accumulated * 2 >= total and index + 1 < len(ordered):
            split_index = index + 1
            break
    return ordered[:split_index], ordered[split_index:]


def _weighted_median_low(pairs: list[tuple[int, int]]) -> int:
    total = sum(count for _value, count in pairs)
    threshold = (total + 1) // 2
    accumulated = 0
    for value, count in pairs:
        accumulated += count
        if accumulated >= threshold:
            return value
    return pairs[-1][0]


def _box_color(box: _Box) -> tuple[int, int, int]:
    channels = []
    for channel in _CHANNELS:
        counter: Counter[int] = Counter()
        for color, count in box:
            counter[color[channel]] += count
        channels.append(_weighted_median_low(sorted(counter.items())))
    return (channels[0], channels[1], channels[2])


def _nearest(color: tuple[int, int, int], palette: list[tuple[int, int, int]]):
    best = palette[0]
    best_distance: int | None = None
    for candidate in sorted(palette):
        distance = sum((a - b) ** 2 for a, b in zip(color, candidate, strict=True))
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best = candidate
    return best


def limit_palette(texture: Image.Image, max_colors: int) -> Image.Image:
    if max_colors < 2:
        raise ProcessingError("INVALID_PALETTE_LIMIT", "调色板颜色上限必须至少为 2")
    histogram = Counter(texture.get_flattened_data())
    if len(histogram) <= max_colors:
        return texture.copy()

    boxes: list[_Box] = [sorted(histogram.items())]
    while len(boxes) < max_colors:
        index = _split_target_index(boxes)
        if index is None:
            break
        box = boxes.pop(index)
        first, second = _split_box(box)
        boxes.insert(index, second)
        boxes.insert(index, first)

    palette = [_box_color(box) for box in boxes]
    mapping = {color: _nearest(color, palette) for color in histogram}
    limited = Image.new("RGB", texture.size)
    limited.putdata([mapping[color] for color in texture.get_flattened_data()])
    return limited
