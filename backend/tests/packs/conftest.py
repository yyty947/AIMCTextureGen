import io
import json
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def pack_zip_factory(tmp_path: Path):
    def create(name: str, members: dict[str, bytes], pack_format: int = 34) -> Path:
        path = tmp_path / name
        payload = {
            "pack": {
                "pack_format": pack_format,
                "description": "AIMCTextureGen synthetic test pack",
            }
        }
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("pack.mcmeta", json.dumps(payload))
            for member_name, data in members.items():
                archive.writestr(member_name, data)
        return path

    return create


@pytest.fixture
def one_pixel_png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (1, 1), (64, 64, 64)).save(buffer, format="PNG")
    return buffer.getvalue()
