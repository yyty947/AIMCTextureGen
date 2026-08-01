"""Frozen rebuild snapshot for the disposable query index."""

from __future__ import annotations

from dataclasses import dataclass

from aimctexturegen.jobs.models import JobSummary
from aimctexturegen.projects.models import ProjectSummary


@dataclass(frozen=True)
class IndexSnapshot:
    projects: tuple[ProjectSummary, ...]
    jobs: tuple[JobSummary, ...]
