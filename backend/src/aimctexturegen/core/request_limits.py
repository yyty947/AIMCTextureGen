from collections.abc import Awaitable, Callable
from typing import Any

from aimctexturegen.core.errors import ApiProblem, problem_response


AsgiMessage = dict[str, Any]
Receive = Callable[[], Awaitable[AsgiMessage]]
Send = Callable[[AsgiMessage], Awaitable[None]]


class _RequestBodyTooLarge(OSError):
    pass


class ImportBodyLimitMiddleware:
    def __init__(self, app, *, max_body_bytes: int) -> None:
        if max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive")
        self._app = app
        self._max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive: Receive, send: Send) -> None:
        if not _is_project_import(scope):
            await self._app(scope, receive, send)
            return

        content_length = _content_length(scope)
        if content_length is not None and content_length > self._max_body_bytes:
            await _too_large_response()(scope, receive, send)
            return

        received_bytes = 0
        limit_exceeded = False
        response_started = False

        async def limited_receive() -> AsgiMessage:
            nonlocal received_bytes, limit_exceeded
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self._max_body_bytes:
                    limit_exceeded = True
                    raise _RequestBodyTooLarge
            return message

        async def guarded_send(message: AsgiMessage) -> None:
            nonlocal response_started
            if limit_exceeded:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self._app(scope, limited_receive, guarded_send)
        except _RequestBodyTooLarge:
            limit_exceeded = True

        if limit_exceeded:
            if response_started:
                raise RuntimeError("Import body limit was exceeded after response start")
            await _too_large_response()(scope, receive, send)


def _is_project_import(scope) -> bool:
    return (
        scope.get("type") == "http"
        and scope.get("method") == "POST"
        and scope.get("path") == "/api/projects/import"
    )


def _content_length(scope) -> int | None:
    values = [
        value
        for name, value in scope.get("headers", ())
        if name.lower() == b"content-length"
    ]
    if len(values) != 1:
        return None
    try:
        value = int(values[0])
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _too_large_response():
    return problem_response(
        ApiProblem(
            status_code=413,
            code="IMPORT_TOO_LARGE",
            stage="uploading",
            user_message="上传请求超过允许大小",
            recommended_actions=("选择更小的 ZIP 资源包后重试",),
            technical_details=None,
        )
    )
