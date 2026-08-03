"""Revision wake-up hints for durable generation job snapshots."""

from __future__ import annotations

import threading
import time
from uuid import UUID


class JobEventBroker:
    """Publish best-effort revision hints; durable job JSON remains truth."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._revisions: dict[tuple[UUID, UUID], int] = {}

    def publish(self, project_id: UUID, job_id: UUID, revision: int) -> None:
        key = (project_id, job_id)
        with self._condition:
            current = self._revisions.get(key)
            if current is None or revision > current:
                self._revisions[key] = revision
            self._condition.notify_all()

    def wait_for_change(
        self,
        project_id: UUID,
        job_id: UUID,
        after_revision: int,
        timeout: float,
    ) -> int | None:
        deadline = time.monotonic() + max(0.0, timeout)
        key = (project_id, job_id)
        with self._condition:
            while True:
                revision = self._revisions.get(key)
                if revision is not None and revision > after_revision:
                    return revision
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=remaining)
