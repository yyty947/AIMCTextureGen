"""Processing-local error type.

The processing package must stay importable without FastAPI or services,
so it raises ProcessingError instead of ApiProblem. Later phases translate
codes into the stable API error envelope.
"""

from __future__ import annotations


class ProcessingError(Exception):
    """A stable, processing-domain failure with a user-safe code and message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
