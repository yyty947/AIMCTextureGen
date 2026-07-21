# Phase 1 Foundation and Java Pack Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended when multi-agent support is explicitly available) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first GPU-free vertical slice: import a Java resource pack into an immutable project snapshot and working copy, select a validated catalog profile from `pack.mcmeta`, compute texture coverage, and display the result in a local React UI through FastAPI.

**Architecture:** The backend owns filesystem access and exposes typed project endpoints. `JavaPackAdapter` validates pack structure, `ProjectWorkspace` creates the snapshot and working copy, and `CatalogRegistry` supplies versioned path metadata; coverage remains a pure comparison over normalized relative paths. The frontend is deliberately thin and calls only the FastAPI contract.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, Pillow, pytest, Node.js 24 LTS, React 19, TypeScript 7, Vite 8, Vitest, Testing Library, npm, and PowerShell.

## Global Constraints

- Supported host: Windows with NVIDIA CUDA; Phase 1 itself must not require CUDA or a GPU.
- v0.1 supports Java resource packs and ordinary, single-texture, opaque, non-animated blocks only.
- Imported ZIPs/directories and `source/` snapshots are immutable; Phase 1 has no endpoint that modifies `pack/` after import.
- The repository contains independently maintained path metadata and no Mojang/Microsoft original textures, model JSON, or complete game assets.
- The application accesses only the user-selected import and the configured project root.
- ZIP validation rejects traversal, absolute paths, Windows device names, case-folding conflicts, and ambiguous pack roots before creating a project.
- `pack.pack_format` selects the catalog profile; `supported_formats` is preserved but never replaces the primary format.
- WebUI accesses imported data only through FastAPI.
- The Phase 1 catalog is explicitly marked as a developer fixture and must not be presented as a complete production compatibility catalog.
- No ComfyUI, model, generation, adoption, export, or GPU logic enters this phase.

---

## Planned File Map

```text
.gitignore                                  Local/runtime exclusions
backend/pyproject.toml                      Backend package and dependency pins
backend/src/aimctexturegen/main.py          FastAPI application factory
backend/src/aimctexturegen/core/errors.py   Stable domain errors and API mapping
backend/src/aimctexturegen/catalog/         Catalog models and registry
backend/src/aimctexturegen/packs/           Java pack inspection and coverage
backend/src/aimctexturegen/projects/        Project manifest and workspace import
backend/src/aimctexturegen/api/projects.py  Project HTTP routes
backend/tests/                              Unit and integration tests
catalogs/java/dev-format-34.json            Developer-only catalog fixture
frontend/package.json                       Frontend dependency pins and scripts
frontend/.node-version                      Phase 1 Node.js runtime pin
frontend/src/api.ts                         Typed FastAPI client
frontend/src/App.tsx                        Import and coverage vertical slice
frontend/src/*.test.tsx                     UI behavior tests
frontend/vite.config.ts                     Vite/Vitest config and API proxy
```

Later phases consume the interfaces named below. Do not rename them casually; if a name changes, update every affected phase plan and `ONBOARDING.md` in the same commit.

---

### Task 1: Backend Application Skeleton and Health Contract

**Files:**
- Modify: `.gitignore`
- Create: `backend/pyproject.toml`
- Create: `backend/src/aimctexturegen/__init__.py`
- Create: `backend/src/aimctexturegen/main.py`
- Create: `backend/tests/test_health.py`

**Interfaces:**
- Produces: `create_app() -> FastAPI`
- Produces: `GET /api/health -> {"status": "ok", "schema_version": 1}`

- [ ] **Step 1: Extend local artifact exclusions**

Add these exact entries without removing existing ignore rules:

```gitignore
.venv/
__pycache__/
.pytest_cache/
.coverage
htmlcov/
frontend/node_modules/
frontend/dist/
runtime/
projects/
*.log
```

- [ ] **Step 2: Write the failing health test**

Create `backend/tests/test_health.py`:

```python
from fastapi.testclient import TestClient

from aimctexturegen.main import create_app


def test_health_contract() -> None:
    client = TestClient(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "schema_version": 1}
```

- [ ] **Step 3: Add the backend package metadata**

Create `backend/pyproject.toml` with fixed Phase 1-compatible dependencies:

```toml
[build-system]
requires = ["hatchling==1.31.0"]
build-backend = "hatchling.build"

[project]
name = "aimctexturegen"
version = "0.1.0.dev0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "fastapi==0.139.2",
  "pydantic==2.13.4",
  "python-multipart==0.0.32",
  "uvicorn[standard]==0.51.0",
]

[project.optional-dependencies]
dev = [
  "httpx==0.28.1",
  "pytest==9.1.1",
  "pytest-cov==7.1.0",
]

[tool.hatch.build.targets.wheel]
packages = ["src/aimctexturegen"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"
```

- [ ] **Step 4: Create the minimal FastAPI application**

Create an empty `backend/src/aimctexturegen/__init__.py`, then create `backend/src/aimctexturegen/main.py`:

```python
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="AIMCTextureGen API", version="0.1.0")

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "schema_version": 1}

    return app


app = create_app()
```

- [ ] **Step 5: Create the isolated environment and verify the test**

Run from the repository root:

```powershell
py -3.12 -m venv .venv
\.\.venv\Scripts\python -m pip install --upgrade pip
\.\.venv\Scripts\python -m pip install -e ".\backend[dev]"
\.\.venv\Scripts\python -m pytest backend\tests\test_health.py -v
```

Expected: one test passes and no package is installed into global Python.

- [ ] **Step 6: Commit the backend skeleton**

```powershell
git add .gitignore backend
git commit -m "build: scaffold FastAPI backend"
```

---

### Task 2: Catalog Contracts and Version Selection

**Files:**
- Create: `backend/src/aimctexturegen/catalog/__init__.py`
- Create: `backend/src/aimctexturegen/catalog/models.py`
- Create: `backend/src/aimctexturegen/catalog/registry.py`
- Create: `catalogs/java/dev-format-34.json`
- Create: `backend/tests/catalog/test_registry.py`

**Interfaces:**
- Produces: `CatalogEntry`, `CatalogProfile`
- Produces: `CatalogRegistry(root: Path)`
- Produces: `CatalogRegistry.for_pack_format(pack_format: int) -> CatalogProfile`
- Raises: `UnsupportedPackFormat(pack_format: int, supported: tuple[int, ...])`

- [ ] **Step 1: Write failing registry tests**

Create `backend/tests/catalog/test_registry.py`:

```python
from pathlib import Path

import pytest

from aimctexturegen.catalog.registry import CatalogRegistry, UnsupportedPackFormat


CATALOG_ROOT = Path(__file__).parents[3] / "catalogs" / "java"


def test_loads_profile_by_primary_pack_format() -> None:
    profile = CatalogRegistry(CATALOG_ROOT).for_pack_format(34)

    assert profile.catalog_id == "java-dev-format-34"
    assert profile.pack_formats == (34,)
    assert [entry.semantic_id for entry in profile.entries] == [
        "minecraft:stone",
        "minecraft:deepslate",
    ]


def test_rejects_unsupported_primary_pack_format() -> None:
    registry = CatalogRegistry(CATALOG_ROOT)

    with pytest.raises(UnsupportedPackFormat) as raised:
        registry.for_pack_format(999)

    assert raised.value.pack_format == 999
    assert raised.value.supported == (34,)
```

- [ ] **Step 2: Run the tests and confirm the missing-module failure**

```powershell
\.\.venv\Scripts\python -m pytest backend\tests\catalog\test_registry.py -v
```

Expected: collection fails because `aimctexturegen.catalog.registry` does not exist.

- [ ] **Step 3: Define strict catalog models**

Create an empty `backend/src/aimctexturegen/catalog/__init__.py` and create `backend/src/aimctexturegen/catalog/models.py`:

```python
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    semantic_id: str
    display_name: str
    category: Literal["block"]
    texture_role: Literal["all"]
    relative_path: str
    prompt_terms: tuple[str, ...]
    mvp_eligible: bool


class CatalogProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    catalog_id: str
    status: Literal["development_fixture", "production"]
    pack_formats: tuple[int, ...] = Field(min_length=1)
    entries: tuple[CatalogEntry, ...]
```

- [ ] **Step 4: Add the developer catalog fixture**

Create `catalogs/java/dev-format-34.json`:

```json
{
  "schema_version": 1,
  "catalog_id": "java-dev-format-34",
  "status": "development_fixture",
  "pack_formats": [34],
  "entries": [
    {
      "semantic_id": "minecraft:stone",
      "display_name": "Stone",
      "category": "block",
      "texture_role": "all",
      "relative_path": "assets/minecraft/textures/block/stone.png",
      "prompt_terms": ["stone block texture", "uniform natural stone"],
      "mvp_eligible": true
    },
    {
      "semantic_id": "minecraft:deepslate",
      "display_name": "Deepslate",
      "category": "block",
      "texture_role": "all",
      "relative_path": "assets/minecraft/textures/block/deepslate.png",
      "prompt_terms": ["deepslate block texture", "dark layered stone"],
      "mvp_eligible": true
    }
  ]
}
```

- [ ] **Step 5: Implement deterministic profile loading**

Create `backend/src/aimctexturegen/catalog/registry.py`:

```python
import json
from pathlib import Path

from aimctexturegen.catalog.models import CatalogProfile


class UnsupportedPackFormat(ValueError):
    def __init__(self, pack_format: int, supported: tuple[int, ...]) -> None:
        self.pack_format = pack_format
        self.supported = supported
        super().__init__(f"Unsupported Java resource pack format: {pack_format}")


class CatalogRegistry:
    def __init__(self, root: Path) -> None:
        self._profiles = self._load(root)
        self._by_format = {
            pack_format: profile
            for profile in self._profiles
            for pack_format in profile.pack_formats
        }
        if len(self._by_format) != sum(len(profile.pack_formats) for profile in self._profiles):
            raise ValueError("A pack format is claimed by more than one catalog profile")

    @staticmethod
    def _load(root: Path) -> tuple[CatalogProfile, ...]:
        profiles = []
        for path in sorted(root.glob("*.json")):
            profiles.append(CatalogProfile.model_validate(json.loads(path.read_text("utf-8"))))
        return tuple(profiles)

    def for_pack_format(self, pack_format: int) -> CatalogProfile:
        profile = self._by_format.get(pack_format)
        if profile is None:
            raise UnsupportedPackFormat(pack_format, tuple(sorted(self._by_format)))
        return profile
```

- [ ] **Step 6: Verify catalog tests and the complete backend suite**

```powershell
\.\.venv\Scripts\python -m pytest backend\tests\catalog\test_registry.py -v
\.\.venv\Scripts\python -m pytest backend\tests -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit catalog contracts**

```powershell
git add backend/src/aimctexturegen/catalog backend/tests/catalog catalogs/java
git commit -m "feat: add versioned Java catalog registry"
```

---

### Task 3: Java Pack Inspection and Safe Member Validation

**Files:**
- Create: `backend/src/aimctexturegen/packs/__init__.py`
- Create: `backend/src/aimctexturegen/packs/models.py`
- Create: `backend/src/aimctexturegen/packs/java_adapter.py`
- Create: `backend/tests/packs/conftest.py`
- Create: `backend/tests/packs/test_java_adapter.py`

**Interfaces:**
- Produces: `PackMetadata(pack_format: int, supported_formats: tuple[int, int] | None)`
- Produces: `InspectedPack(source: Path, source_kind: Literal["zip", "directory"], pack_root: PurePosixPath, metadata: PackMetadata, normalized_files: frozenset[str])`
- Produces: `JavaPackAdapter.inspect(source: Path) -> InspectedPack`
- Raises: `PackValidationError(code: str, user_message: str)`

- [ ] **Step 1: Add fixture helpers that create assets without Mojang content**

Create `backend/tests/packs/conftest.py`:

```python
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
```

Add `Pillow==12.3.0` to `backend/pyproject.toml` dependencies before running these tests.

- [ ] **Step 2: Write inspection and safety tests**

Create `backend/tests/packs/test_java_adapter.py` with these cases:

```python
import json
import zipfile
from pathlib import Path

import pytest

from aimctexturegen.packs.java_adapter import JavaPackAdapter, PackValidationError


def test_inspects_root_pack_and_preserves_primary_format(pack_zip_factory, one_pixel_png) -> None:
    source = pack_zip_factory(
        "valid.zip",
        {"assets/minecraft/textures/block/stone.png": one_pixel_png},
    )

    inspected = JavaPackAdapter().inspect(source)

    assert inspected.source_kind == "zip"
    assert inspected.pack_root.as_posix() == "."
    assert inspected.metadata.pack_format == 34
    assert inspected.normalized_files == frozenset(
        {"pack.mcmeta", "assets/minecraft/textures/block/stone.png"}
    )


def test_preserves_supported_formats_without_replacing_primary(tmp_path: Path) -> None:
    source = tmp_path / "range.zip"
    metadata = {
        "pack": {
            "pack_format": 34,
            "supported_formats": {"min_inclusive": 34, "max_inclusive": 48},
            "description": "synthetic",
        }
    }
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("pack.mcmeta", json.dumps(metadata))

    inspected = JavaPackAdapter().inspect(source)

    assert inspected.metadata.pack_format == 34
    assert inspected.metadata.supported_formats == (34, 48)


@pytest.mark.parametrize(
    "unsafe_name",
    ["../escape.txt", "/absolute.txt", "C:/drive.txt", "assets/CON/file.txt"],
)
def test_rejects_unsafe_zip_member(tmp_path: Path, unsafe_name: str) -> None:
    source = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("pack.mcmeta", '{"pack":{"pack_format":34,"description":"synthetic"}}')
        archive.writestr(unsafe_name, b"unsafe")

    with pytest.raises(PackValidationError) as raised:
        JavaPackAdapter().inspect(source)

    assert raised.value.code == "UNSAFE_PACK_PATH"
```

Also add tests for: missing `pack.mcmeta`, malformed JSON, missing/non-integer `pack_format`, two possible pack roots, case-folding duplicate paths, a valid directory source, and exactly one nested wrapper directory containing `pack.mcmeta`.

- [ ] **Step 3: Run the tests and confirm the missing implementation**

```powershell
\.\.venv\Scripts\python -m pip install -e ".\backend[dev]"
\.\.venv\Scripts\python -m pytest backend\tests\packs\test_java_adapter.py -v
```

Expected: collection fails because the pack adapter modules do not exist.

- [ ] **Step 4: Define immutable inspection models**

Create `backend/src/aimctexturegen/packs/models.py`:

```python
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict


class PackMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    pack_format: int
    supported_formats: tuple[int, int] | None = None


class InspectedPack(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    source: Path
    source_kind: Literal["zip", "directory"]
    pack_root: PurePosixPath
    metadata: PackMetadata
    normalized_files: frozenset[str]
```

- [ ] **Step 5: Implement validation before extraction**

Create an empty `backend/src/aimctexturegen/packs/__init__.py`. In `java_adapter.py`, implement `PackValidationError`, path normalization, Windows reserved-name rejection, case-folding conflict detection, root discovery, metadata parsing, and `inspect`. Use `PurePosixPath` for archive members and `Path.resolve()` plus `is_relative_to()` for directory entries. Do not call `ZipFile.extractall`.

The public class must have this exact surface:

```python
class PackValidationError(ValueError):
    def __init__(self, code: str, user_message: str) -> None:
        self.code = code
        self.user_message = user_message
        super().__init__(user_message)


class JavaPackAdapter:
    def inspect(self, source: Path) -> InspectedPack:
        if source.is_file() and source.suffix.casefold() == ".zip":
            return self._inspect_zip(source)
        if source.is_dir():
            return self._inspect_directory(source)
        raise PackValidationError("UNSUPPORTED_SOURCE", "请选择 ZIP 文件或资源包目录")
```

Safety helpers must return normalized forward-slash paths, reject empty/dot-only file names, reject any `..` segment, reject absolute/drive/UNC forms, and reject these Windows device stems case-insensitively: `CON`, `PRN`, `AUX`, `NUL`, `COM1` through `COM9`, and `LPT1` through `LPT9`.

- [ ] **Step 6: Run all pack and backend tests**

```powershell
\.\.venv\Scripts\python -m pytest backend\tests\packs -v
\.\.venv\Scripts\python -m pytest backend\tests -v
```

Expected: all tests pass; test output contains no writes outside pytest temporary directories.

- [ ] **Step 7: Commit Java pack inspection**

```powershell
git add backend/pyproject.toml backend/src/aimctexturegen/packs backend/tests/packs
git commit -m "feat: validate Java resource pack inputs"
```

---

### Task 4: Project Workspace Import and Immutable Snapshot

**Files:**
- Create: `backend/src/aimctexturegen/projects/__init__.py`
- Create: `backend/src/aimctexturegen/projects/models.py`
- Create: `backend/src/aimctexturegen/projects/workspace.py`
- Create: `backend/tests/projects/test_workspace.py`

**Interfaces:**
- Produces: `ProjectManifest`
- Produces: `ProjectWorkspace(root: Path, adapter: JavaPackAdapter, catalogs: CatalogRegistry)`
- Produces: `ProjectWorkspace.import_pack(source: Path, project_name: str) -> ProjectManifest`
- Produces project paths: `project.json`, `source/imported-pack.zip`, and `pack/`

- [ ] **Step 1: Write failing import tests**

Create `backend/tests/projects/test_workspace.py` covering both ZIP and directory sources. The primary test must assert:

```python
def test_import_creates_snapshot_and_working_copy(
    tmp_path, pack_zip_factory, one_pixel_png
) -> None:
    source = pack_zip_factory(
        "source.zip",
        {"assets/minecraft/textures/block/stone.png": one_pixel_png},
    )
    source_hash_before = sha256(source.read_bytes()).hexdigest()
    workspace = build_workspace(tmp_path / "projects")

    manifest = workspace.import_pack(source, "Synthetic Pack")

    project_root = tmp_path / "projects" / str(manifest.project_id)
    assert manifest.project_name == "Synthetic Pack"
    assert manifest.java_pack_format == 34
    assert manifest.catalog_id == "java-dev-format-34"
    assert manifest.source_sha256 == source_hash_before
    assert (project_root / "source" / "imported-pack.zip").is_file()
    assert (project_root / "pack" / "pack.mcmeta").is_file()
    assert source_hash_before == sha256(source.read_bytes()).hexdigest()
```

The same file must assert that failed validation leaves no project directory, directory import produces a deterministic local ZIP snapshot, and project names containing filesystem separators do not affect generated paths.

- [ ] **Step 2: Run tests and verify the expected import failure**

```powershell
\.\.venv\Scripts\python -m pytest backend\tests\projects\test_workspace.py -v
```

Expected: collection fails because project workspace modules do not exist.

- [ ] **Step 3: Define the versioned project manifest**

Create `backend/src/aimctexturegen/projects/models.py` with a strict frozen `ProjectManifest` containing these exact fields:

```python
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ProjectManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    project_id: UUID
    project_name: str
    edition: str
    java_pack_format: int
    supported_formats: tuple[int, int] | None
    catalog_id: str
    source_sha256: str
    created_at: datetime
    updated_at: datetime
```

Use `schema_version=1` and `edition="java"` in Phase 1.

- [ ] **Step 4: Implement staged import**

Create `ProjectWorkspace`. `import_pack` must perform operations in this order:

1. inspect the source completely;
2. resolve the catalog by the primary `pack_format`;
3. allocate a UUID but create only `<project-id>.tmp`;
4. create or copy `source/imported-pack.zip` and compute SHA-256;
5. copy directory members or stream validated ZIP members into `pack/` without `extractall`;
6. write UTF-8 `project.json` with `model_dump_json(indent=2)`;
7. re-open and validate `project.json`;
8. atomically rename the temporary project directory to `<project-id>`;
9. on failure, delete only the verified `<project-id>.tmp` directory inside the configured project root.

The constructor and method signatures must be:

```python
class ProjectWorkspace:
    def __init__(
        self,
        root: Path,
        adapter: JavaPackAdapter,
        catalogs: CatalogRegistry,
    ) -> None:
        self._root = root.resolve()
        self._adapter = adapter
        self._catalogs = catalogs

    def import_pack(self, source: Path, project_name: str) -> ProjectManifest:
        inspected = self._adapter.inspect(source.resolve())
        profile = self._catalogs.for_pack_format(inspected.metadata.pack_format)
        return self._create_project(inspected, profile, project_name.strip())
```

Reject an empty trimmed project name with `PackValidationError("INVALID_PROJECT_NAME", "项目名称不能为空")`.

- [ ] **Step 5: Run workspace and regression tests**

```powershell
\.\.venv\Scripts\python -m pytest backend\tests\projects -v
\.\.venv\Scripts\python -m pytest backend\tests -v
```

Expected: all tests pass; source hashes are unchanged; failed imports leave no final or temporary project directory.

- [ ] **Step 6: Commit workspace import**

```powershell
git add backend/src/aimctexturegen/projects backend/tests/projects
git commit -m "feat: import packs into isolated project workspaces"
```

---

### Task 5: Coverage Classification

**Files:**
- Create: `backend/src/aimctexturegen/packs/coverage.py`
- Create: `backend/tests/packs/test_coverage.py`

**Interfaces:**
- Produces: `CoverageStatus = Literal["covered", "missing"]`
- Produces: `CoverageItem(semantic_id, display_name, relative_path, mvp_eligible, status)`
- Produces: `CoverageReport(catalog_id, catalog_status, covered_count, missing_count, unknown_paths, items)`
- Produces: `classify_coverage(pack_root: Path, profile: CatalogProfile) -> CoverageReport`

- [ ] **Step 1: Write failing coverage tests**

Create a temporary working pack containing valid synthetic `stone.png`, an unknown square PNG, and no `deepslate.png`. Assert that `stone` is covered, `deepslate` is missing, the unknown file is preserved in `unknown_paths`, and a wrongly cased `Stone.png` does not cover the canonical lowercase path.

Use this central assertion:

```python
report = classify_coverage(pack_root, profile)

assert report.covered_count == 1
assert report.missing_count == 1
assert report.catalog_status == "development_fixture"
assert [(item.semantic_id, item.status) for item in report.items] == [
    ("minecraft:stone", "covered"),
    ("minecraft:deepslate", "missing"),
]
assert report.unknown_paths == (
    "assets/minecraft/textures/block/custom_test.png",
)
```

- [ ] **Step 2: Run the focused test and confirm the missing function**

```powershell
\.\.venv\Scripts\python -m pytest backend\tests\packs\test_coverage.py -v
```

Expected: collection fails because `packs.coverage` does not exist.

- [ ] **Step 3: Implement exact-path coverage**

Define frozen Pydantic report models and implement `classify_coverage` so it copies both `profile.catalog_id` and `profile.status` into `catalog_id` and `catalog_status`, then:

- enumerates files beneath `pack_root` without following directory symlinks;
- normalizes separators to `/` but does not lowercase paths;
- treats only a decodable PNG at the exact catalog path as covered;
- raises a validation error for a corrupt PNG at a catalog path;
- keeps non-catalog files unchanged and reports decodable square PNG files under `assets/*/textures/` as unknown reference candidates;
- sorts items by catalog order and unknown paths lexicographically.

Use `PIL.Image.verify()` for decode validation and reopen the image before reading dimensions; do not retain decoded pixel data.

- [ ] **Step 4: Run coverage and all backend tests**

```powershell
\.\.venv\Scripts\python -m pytest backend\tests\packs\test_coverage.py -v
\.\.venv\Scripts\python -m pytest backend\tests -v
```

Expected: all tests pass and coverage results remain stable across repeated runs.

- [ ] **Step 5: Commit coverage classification**

```powershell
git add backend/src/aimctexturegen/packs/coverage.py backend/tests/packs/test_coverage.py
git commit -m "feat: classify Java texture coverage"
```

---

### Task 6: Project API and Stable Error Envelope

**Files:**
- Create: `backend/src/aimctexturegen/core/__init__.py`
- Create: `backend/src/aimctexturegen/core/errors.py`
- Create: `backend/src/aimctexturegen/api/__init__.py`
- Create: `backend/src/aimctexturegen/api/projects.py`
- Modify: `backend/src/aimctexturegen/main.py`
- Create: `backend/tests/api/test_projects.py`

**Interfaces:**
- Produces: `POST /api/projects/import`
- Produces: `GET /api/projects/{project_id}`
- Produces: `GET /api/projects/{project_id}/coverage`
- Produces error JSON: `{"code", "stage", "user_message", "recommended_actions", "technical_details"}`

- [ ] **Step 1: Write API contract tests**

Use `TestClient`, a pytest temporary project root, and the synthetic ZIP factory. The successful import assertion must be:

```python
with source.open("rb") as upload:
    response = client.post(
        "/api/projects/import",
        data={"project_name": "Synthetic Pack"},
        files={"pack": ("source.zip", upload, "application/zip")},
    )

assert response.status_code == 201
body = response.json()
assert body["project_name"] == "Synthetic Pack"
assert body["java_pack_format"] == 34
assert body["catalog_id"] == "java-dev-format-34"
```

Also assert a rejected unsafe ZIP returns HTTP 400 with `code="UNSAFE_PACK_PATH"`, and an unknown project returns HTTP 404 with `code="PROJECT_NOT_FOUND"`.

- [ ] **Step 2: Run API tests and confirm route absence**

```powershell
\.\.venv\Scripts\python -m pytest backend\tests\api\test_projects.py -v
```

Expected: the import request returns 404 because the route is not registered.

- [ ] **Step 3: Add explicit application dependencies**

Define an `AppServices` dataclass containing `workspace`, `catalogs`, and `project_root`. Change `create_app` to accept `project_root: Path | None = None` and `catalog_root: Path | None = None`, construct services once, store them in `app.state.services`, and register the projects router. Tests pass temporary roots; runtime defaults resolve from the repository configuration, never from the current shell directory implicitly.

- [ ] **Step 4: Implement routes with bounded uploads**

`POST /api/projects/import` must stream the upload to a temporary file under the configured project root, reject files over a named `MAX_IMPORT_BYTES` constant, call `ProjectWorkspace.import_pack`, and remove only that temporary upload in `finally`. The endpoint never accepts an arbitrary client filesystem path.

The two GET endpoints load `project.json` from a UUID project directory and recompute coverage from the current working copy. Map known domain errors to the stable envelope; unexpected errors preserve technical details in logs but return a generic user message.

- [ ] **Step 5: Run API, backend, and import immutability tests**

```powershell
\.\.venv\Scripts\python -m pytest backend\tests\api -v
\.\.venv\Scripts\python -m pytest backend\tests -v
```

Expected: all tests pass; temporary uploads are gone after success and failure; imported snapshots remain unchanged.

- [ ] **Step 6: Commit the project API**

```powershell
git add backend/src/aimctexturegen/api backend/src/aimctexturegen/core backend/src/aimctexturegen/main.py backend/tests/api
git commit -m "feat: expose project import and coverage API"
```

---

### Task 7: React Import and Coverage Vertical Slice

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/package-lock.json`
- Create: `frontend/.node-version`
- Create: `frontend/index.html`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/api.ts`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/App.test.tsx`
- Create: `frontend/src/styles.css`

**Interfaces:**
- Consumes: Phase 1 project API contracts
- Produces: project-name input, ZIP picker, explicit import action, resource-format summary, covered/missing counts, missing-item list, unknown-path count, and readable error panel

- [ ] **Step 1: Create pinned frontend metadata**

Create `frontend/.node-version` containing `24.18.0`, the LTS patch verified when this plan was written. Then create `frontend/package.json`:

```json
{
  "name": "aimctexturegen-frontend",
  "private": true,
  "version": "0.1.0-dev",
  "type": "module",
  "engines": {
    "node": ">=24.18.0 <25"
  },
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "test": "vitest run"
  },
  "dependencies": {
    "react": "19.2.7",
    "react-dom": "19.2.7"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "7.0.0",
    "@testing-library/react": "16.3.2",
    "@testing-library/user-event": "14.6.1",
    "@types/react": "19.2.17",
    "@types/react-dom": "19.2.3",
    "@vitejs/plugin-react": "6.0.3",
    "jsdom": "29.1.1",
    "typescript": "7.0.2",
    "vite": "8.1.5",
    "vitest": "4.1.10"
  }
}
```

Run `node --version` first and require `v24.18.0` for the initial lockfile. Run `npm install` inside `frontend` and commit the resulting `package-lock.json`. Do not hand-edit the lockfile. If a pin cannot resolve, stop and update this dated plan with registry evidence instead of silently selecting another version.

- [ ] **Step 2: Write the failing UI behavior test**

Create `frontend/src/App.test.tsx` using `vi.stubGlobal("fetch", ...)`. The test must select a synthetic `File`, click the Chinese-labeled import button, and assert the rendered summary includes `资源格式 34`, `已覆盖 1`, `未覆盖 1`, and `Deepslate`. A second test returns the API error envelope and asserts `不安全的资源包路径` is visible.

- [ ] **Step 3: Run the UI tests and verify the missing component**

```powershell
Push-Location frontend
npm test
Pop-Location
```

Expected: test compilation fails because `App.tsx` and the API client do not exist.

- [ ] **Step 4: Implement a typed API client**

Create `frontend/src/api.ts` with exact TypeScript types for `ProjectManifest`, `CoverageItem`, `CoverageReport`, and `ApiError`. `CoverageReport.catalogStatus` must be the union `"development_fixture" | "production"` after JSON key conversion. Export:

```typescript
export async function importProject(projectName: string, pack: File): Promise<ProjectManifest>
export async function getCoverage(projectId: string): Promise<CoverageReport>
```

`importProject` sends `FormData` with `project_name` and `pack`. Both functions parse the error envelope and throw an `ApiRequestError` carrying `code`, `userMessage`, `recommendedActions`, and `technicalDetails`.

- [ ] **Step 5: Implement the accessible import screen**

`App.tsx` must use native form controls with associated labels, disable submission until a non-empty name and ZIP are selected, expose `aria-busy` during import, and render errors in an element with `role="alert"`. After import, fetch coverage and show:

- project name and Java resource format;
- a clear `development_fixture` warning while the fixture catalog is active;
- covered and missing counts;
- a list of missing eligible entries with display name and relative path;
- unknown/custom count without deleting or hiding those files.

Do not add generation controls, model settings, desktop APIs, or direct path fields in this phase.

- [ ] **Step 6: Configure Vite and run tests/build**

Configure `/api` to proxy to `http://127.0.0.1:8000` during development and configure Vitest with `environment: "jsdom"` and a setup file that imports `@testing-library/jest-dom/vitest`.

Run:

```powershell
Push-Location frontend
npm test
npm run build
Pop-Location
```

Expected: UI tests pass and Vite produces `frontend/dist` without TypeScript errors.

- [ ] **Step 7: Commit the frontend vertical slice**

```powershell
git add frontend
git commit -m "feat: add resource pack import UI"
```

---

### Task 8: Phase Integration Evidence and Handoff

**Files:**
- Create: `backend/tests/integration/test_import_flow.py`
- Modify: `README.md`
- Modify: `ONBOARDING.md`
- Modify: `docs/superpowers/plans/2026-07-21-phase-1-foundation-and-import.md`

**Interfaces:**
- Verifies the complete Phase 1 backend flow without GPU
- Produces true development commands and Phase 2 handoff state

- [ ] **Step 1: Add a full backend import-flow test**

The integration test must create a synthetic ZIP, record its SHA-256, call the import endpoint, call the coverage endpoint, reopen `project.json`, reopen `source/imported-pack.zip`, and assert:

- the input and snapshot hashes match the recorded hash;
- `pack.mcmeta` and the synthetic covered PNG exist in `pack/`;
- the fixture catalog reports exactly one covered and one missing item;
- no file outside the pytest temporary project root changes;
- no ComfyUI, CUDA, network, or model dependency is used.

- [ ] **Step 2: Run the complete automated gate**

```powershell
\.\.venv\Scripts\python -m pytest backend\tests --cov=aimctexturegen --cov-report=term-missing
Push-Location frontend
npm test
npm run build
Pop-Location
git diff --check
```

Expected: backend tests, frontend tests and frontend build pass; `git diff --check` prints nothing. Coverage percentage is recorded as evidence but Phase 1 does not impose an arbitrary numeric threshold.

- [ ] **Step 3: Run the local browser smoke test**

In terminal one:

```powershell
\.\.venv\Scripts\python -m uvicorn aimctexturegen.main:app --app-dir backend\src --host 127.0.0.1 --port 8000
```

In terminal two:

```powershell
Push-Location frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

Open `http://127.0.0.1:5173`, import a synthetic test ZIP, and verify the development-catalog warning, format 34, one covered item, one missing item, and the missing Deepslate path. Stop both processes after the smoke test.

- [ ] **Step 4: Update living documentation from observed facts**

Update `README.md` with only commands that succeeded. Update `ONBOARDING.md` with the final test results, the last completed commit, and Phase 2 as the next work item. Mark every completed checkbox in this plan. Do not claim support for a production `pack_format` catalog while the profile status remains `development_fixture`.

- [ ] **Step 5: Commit Phase 1 evidence and handoff**

```powershell
git add backend/tests/integration README.md ONBOARDING.md docs/superpowers/plans/2026-07-21-phase-1-foundation-and-import.md
git commit -m "test: verify Phase 1 import flow"
```

---

## Self-Review Record

- **Spec coverage:** Phase 1 covers the non-GPU foundation required by design sections 3–6, the import-related parts of sections 9–12, and preserves all boundaries needed by later phases. Generation, processing, jobs, adoption, export and launcher work are deliberately assigned to later independently gated plans in the roadmap.
- **Placeholder scan:** This plan contains no `TBD`, implementation placeholders, or unnamed error-handling steps. Production catalog provenance and runtime/model pins are explicitly phase-gated decisions, not hidden work in Phase 1.
- **Type consistency:** `CatalogRegistry`, `JavaPackAdapter`, `ProjectWorkspace`, `ProjectManifest`, and `CoverageReport` names are stable across tasks. API and frontend types consume those same persisted identifiers.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-21-phase-1-foundation-and-import.md`.

Execution options when implementation begins:

1. **Subagent-Driven** — use `superpowers:subagent-driven-development` only when the user explicitly requests subagents or applicable repository instructions authorize them.
2. **Inline Execution** — use `superpowers:executing-plans` in the current session with review checkpoints.

Do not begin either option until the user explicitly asks to start implementation.
