"""Atomic install-operation records with monotonic revisions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import ConfigDict, Field
from pydantic import BaseModel

from aimctexturegen.comfy.errors import InstallStateError
from aimctexturegen.core.atomic_files import atomic_replace_bytes

InstallOperationState = Literal[
    "planned",
    "downloading",
    "extracting",
    "installing",
    "completed",
    "failed",
    "canceled",
]

_TERMINAL_STATES = frozenset({"completed", "failed", "canceled"})


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class InstallErrorRecord(_StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class InstallOperation(_StrictModel):
    operation_id: UUID
    runtime_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    plan_digest: str = Field(min_length=64, max_length=64)
    accepted_component_ids: tuple[str, ...]
    state: InstallOperationState
    revision: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    error: InstallErrorRecord | None = None


class InstallOperationStore:
    """Persist strict install operations below an injected state root."""

    def __init__(self, state_root: Path) -> None:
        self._operations_dir = Path(state_root) / "install-operations"

    def _path(self, operation_id: UUID) -> Path:
        return self._operations_dir / f"{operation_id}.json"

    def create(
        self,
        *,
        runtime_id: str,
        profile_id: str,
        plan_digest: str,
        accepted_component_ids: tuple[str, ...],
    ) -> InstallOperation:
        self._operations_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)
        operation = InstallOperation(
            operation_id=uuid4(),
            runtime_id=runtime_id,
            profile_id=profile_id,
            plan_digest=plan_digest,
            accepted_component_ids=tuple(accepted_component_ids),
            state="planned",
            revision=1,
            created_at=now,
            updated_at=now,
        )
        self._write(operation)
        return operation

    def transition(
        self,
        operation: InstallOperation,
        state: InstallOperationState,
        *,
        error: InstallErrorRecord | None = None,
    ) -> InstallOperation:
        if operation.state in _TERMINAL_STATES:
            raise InstallStateError(
                f"cannot transition terminal operation "
                f"{operation.operation_id} from {operation.state}"
            )
        if state == "failed" and error is None:
            raise InstallStateError("failed operations require an error record")
        if state != "failed" and error is not None:
            raise InstallStateError("error records only apply to failed operations")
        updated = operation.model_copy(
            update={
                "state": state,
                "revision": operation.revision + 1,
                "updated_at": datetime.now(UTC),
                "error": error,
            }
        )
        self._write(updated)
        return updated

    def get(self, operation_id: UUID) -> InstallOperation | None:
        path = self._path(operation_id)
        if not path.is_file():
            return None
        return InstallOperation.model_validate_json(
            path.read_text(encoding="utf-8")
        )

    def list_operations(self) -> tuple[InstallOperation, ...]:
        if not self._operations_dir.is_dir():
            return ()
        operations = [
            InstallOperation.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(self._operations_dir.glob("*.json"))
        ]
        return tuple(
            sorted(operations, key=lambda operation: operation.created_at)
        )

    def recover_interrupted(self) -> tuple[InstallOperation, ...]:
        recovered: list[InstallOperation] = []
        for operation in self.list_operations():
            if operation.state in _TERMINAL_STATES:
                continue
            recovered.append(
                self.transition(
                    operation,
                    "failed",
                    error=InstallErrorRecord(
                        code="INSTALL_INTERRUPTED",
                        message="installation was interrupted by a restart",
                    ),
                )
            )
        return tuple(recovered)

    def _write(self, operation: InstallOperation) -> None:
        payload = json.dumps(
            operation.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        atomic_replace_bytes(
            self._path(operation.operation_id),
            payload,
            validator=lambda readback: json.loads(readback),
        )
