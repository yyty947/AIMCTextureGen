from __future__ import annotations

import threading
import time
from uuid import UUID

from aimctexturegen.generation.events import JobEventBroker


PROJECT_ID = UUID("77777777-1111-4111-8111-111111111111")
JOB_ID = UUID("88888888-2222-4222-8222-222222222222")


def test_wait_for_change_returns_next_revision_after_publish() -> None:
    broker = JobEventBroker()
    observed: list[int | None] = []

    def waiter() -> None:
        observed.append(
            broker.wait_for_change(
                PROJECT_ID,
                JOB_ID,
                after_revision=3,
                timeout=1.0,
            )
        )

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    time.sleep(0.05)
    broker.publish(PROJECT_ID, JOB_ID, 4)
    thread.join(timeout=1.0)

    assert observed == [4]


def test_wait_for_change_times_out_without_new_revision() -> None:
    broker = JobEventBroker()

    assert (
        broker.wait_for_change(
            PROJECT_ID,
            JOB_ID,
            after_revision=0,
            timeout=0.05,
        )
        is None
    )


def test_wait_for_change_ignores_stale_revision_for_another_job() -> None:
    broker = JobEventBroker()
    other_job_id = UUID("99999999-3333-4333-8333-333333333333")
    observed: list[int | None] = []

    def waiter() -> None:
        observed.append(
            broker.wait_for_change(
                PROJECT_ID,
                JOB_ID,
                after_revision=5,
                timeout=0.5,
            )
        )

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    time.sleep(0.05)
    broker.publish(PROJECT_ID, other_job_id, 7)
    broker.publish(PROJECT_ID, JOB_ID, 5)
    broker.publish(PROJECT_ID, JOB_ID, 6)
    thread.join(timeout=1.0)

    assert observed == [6]
