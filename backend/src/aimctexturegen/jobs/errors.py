"""Job-domain errors independent of transport and persistence."""

from __future__ import annotations


class JobError(Exception):
    """A stable, path-free job-domain failure."""

    def __init__(self, code: str, user_message: str) -> None:
        self.code = code
        self.user_message = user_message
        super().__init__(user_message)
