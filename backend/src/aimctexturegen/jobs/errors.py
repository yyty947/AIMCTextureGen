"""Job-domain errors independent of transport and persistence."""

from __future__ import annotations


class JobError(Exception):
    """A stable job failure with opt-in transport-safe technical detail."""

    def __init__(
        self,
        code: str,
        user_message: str,
        *,
        technical_details: str | None = None,
        expose_technical_details: bool = False,
    ) -> None:
        self.code = code
        self.user_message = user_message
        self.safe_technical_details = (
            technical_details if expose_technical_details else None
        )
        super().__init__(user_message)
