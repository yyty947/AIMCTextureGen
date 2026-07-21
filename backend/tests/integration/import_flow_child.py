from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


FORBIDDEN_RUNTIME_PREFIXES = frozenset(
    {
        "comfy",
        "cuda",
        "diffusers",
        "huggingface_hub",
        "nvidia",
        "torch",
        "transformers",
    }
)
NETWORK_AUDIT_EVENTS = frozenset(
    {
        "socket.bind",
        "socket.connect",
        "socket.getaddrinfo",
        "socket.gethostbyaddr",
        "socket.gethostbyname",
        "socket.gethostname",
        "socket.getnameinfo",
        "socket.sendmsg",
        "socket.sendto",
    }
)
SINGLE_PATH_MUTATIONS = {
    "os.chmod": 0,
    "os.chown": 0,
    "os.mkdir": 0,
    "os.remove": 0,
    "os.removexattr": 0,
    "os.rmdir": 0,
    "os.setxattr": 0,
    "os.symlink": 1,
    "os.truncate": 0,
    "os.utime": 0,
    "shutil.copyfile": 1,
    "shutil.copytree": 1,
    "shutil.rmtree": 0,
    "tempfile.mkdtemp": 0,
    "tempfile.mkstemp": 0,
}
TWO_PATH_MUTATIONS = {
    "os.link": (0, 1),
    "os.rename": (0, 1),
    "os.replace": (0, 1),
    "shutil.move": (0, 1),
}
WRITE_OPEN_FLAGS = (
    os.O_WRONLY
    | os.O_RDWR
    | os.O_APPEND
    | os.O_CREAT
    | os.O_TRUNC
    | getattr(os, "O_TMPFILE", 0)
)
PROCESS_AUDIT_EVENTS = frozenset(
    {
        "os.exec",
        "os.posix_spawn",
        "os.spawn",
        "os.startfile",
        "os.system",
        "subprocess.Popen",
    }
)


class IsolationPolicyViolation(RuntimeError):
    pass


def _loaded_forbidden_runtime_modules() -> tuple[str, ...]:
    return tuple(
        sorted(
            module_name
            for module_name in sys.modules
            if module_name.split(".", 1)[0].casefold()
            in FORBIDDEN_RUNTIME_PREFIXES
        )
    )


def _event_path(value: object) -> Path | None:
    if value is None or isinstance(value, int):
        return None
    try:
        raw_path = os.fsdecode(value)
    except TypeError as error:
        raise IsolationPolicyViolation(
            f"filesystem audit event supplied an unsupported path: {value!r}"
        ) from error
    return Path(raw_path).resolve(strict=False)


def _require_allowed_path(
    value: object,
    *,
    event: str,
    allowed_root: Path,
) -> None:
    path = _event_path(value)
    if path is None:
        return
    if path != allowed_root and not path.is_relative_to(allowed_root):
        raise IsolationPolicyViolation(
            f"filesystem mutation outside allowed project root: {event}: {path}"
        )


def _open_requests_write(mode: object, flags: object) -> bool:
    return (
        isinstance(mode, str)
        and any(character in mode for character in "wax+")
    ) or (isinstance(flags, int) and bool(flags & WRITE_OPEN_FLAGS))


def _install_isolation_policy(allowed_root: Path) -> None:
    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event in NETWORK_AUDIT_EVENTS:
            raise IsolationPolicyViolation(f"network audit event blocked: {event}")
        if event in PROCESS_AUDIT_EVENTS:
            raise IsolationPolicyViolation(f"process audit event blocked: {event}")
        if event == "open":
            path, mode, flags = args
            if _open_requests_write(mode, flags):
                _require_allowed_path(
                    path,
                    event=event,
                    allowed_root=allowed_root,
                )
            return
        path_index = SINGLE_PATH_MUTATIONS.get(event)
        if path_index is not None:
            _require_allowed_path(
                args[path_index],
                event=event,
                allowed_root=allowed_root,
            )
            return
        path_indexes = TWO_PATH_MUTATIONS.get(event)
        if path_indexes is not None:
            for path_index in path_indexes:
                _require_allowed_path(
                    args[path_index],
                    event=event,
                    allowed_root=allowed_root,
                )

    sys.addaudithook(audit)


async def _call_import_flow(
    app: Any,
    source_name: str,
    source_bytes: bytes,
) -> tuple[Any, Any]:
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        imported = await client.post(
            "/api/projects/import",
            data={"project_name": "Phase 1 Synthetic Pack"},
            files={"pack": (source_name, source_bytes, "application/zip")},
        )
        if imported.status_code != 201:
            return imported, imported
        coverage = await client.get(
            f"/api/projects/{imported.json()['project_id']}/coverage"
        )
        return imported, coverage


def main() -> None:
    if len(sys.argv) not in (4, 5):
        raise SystemExit(
            "usage: import_flow_child.py PROJECT_ROOT SOURCE_ZIP CATALOG_ROOT "
            "[OUTSIDE_WRITE_PROBE|--probe-network]"
        )
    sys.dont_write_bytecode = True
    project_root = Path(sys.argv[1]).resolve(strict=True)
    source = Path(sys.argv[2]).resolve(strict=True)
    catalog_root = Path(sys.argv[3]).resolve(strict=True)
    import asyncio

    # Windows creates an internal loopback socketpair while constructing the
    # asyncio harness. Build that test-only infrastructure before enforcing the
    # policy; no application module has been imported yet.
    event_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(event_loop)
    forbidden_before = _loaded_forbidden_runtime_modules()
    if forbidden_before:
        raise IsolationPolicyViolation(
            f"forbidden runtime modules loaded before app import: {forbidden_before!r}"
        )
    _install_isolation_policy(project_root)

    if len(sys.argv) == 5:
        try:
            if sys.argv[4] == "--probe-network":
                import socket

                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
                    connection.connect_ex(("127.0.0.1", 9))
                raise AssertionError("network probe was not blocked")
            probe = Path(sys.argv[4])
            probe.write_text("this write must be blocked", encoding="utf-8")
            probe.unlink()
            raise AssertionError("outside-write probe was not blocked")
        finally:
            event_loop.close()

    from aimctexturegen.main import create_app

    app = create_app(project_root=project_root, catalog_root=catalog_root)
    source_bytes = source.read_bytes()
    try:
        imported, coverage = event_loop.run_until_complete(
            _call_import_flow(app, source.name, source_bytes)
        )
    finally:
        event_loop.close()
    forbidden_after = _loaded_forbidden_runtime_modules()
    if forbidden_after != forbidden_before:
        raise IsolationPolicyViolation(
            "forbidden runtime modules loaded during import flow: "
            f"before={forbidden_before!r}; after={forbidden_after!r}"
        )

    print(
        json.dumps(
            {
                "import_status": imported.status_code,
                "import_body": imported.json(),
                "coverage_status": coverage.status_code,
                "coverage_body": coverage.json(),
                "forbidden_modules_before": forbidden_before,
                "forbidden_modules_after": forbidden_after,
                "isolation_policy_active": True,
                "dont_write_bytecode": sys.dont_write_bytecode,
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
