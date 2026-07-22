from dataclasses import dataclass
from typing import BinaryIO, Literal

from fastapi import Request
from python_multipart import MultipartParser
from python_multipart.exceptions import FormParserError, MultipartParseError
from python_multipart.multipart import parse_options_header

from aimctexturegen.projects.models import MAX_PROJECT_NAME_LENGTH


MAX_PROJECT_NAME_UTF8_BYTES = MAX_PROJECT_NAME_LENGTH * 4
MAX_MULTIPART_BOUNDARY_BYTES = 200
MAX_MULTIPART_HEADERS_PER_PART = 8
MAX_MULTIPART_HEADER_FIELD_BYTES = 128
MAX_MULTIPART_HEADER_VALUE_BYTES = 1024
MAX_MULTIPART_PART_HEADER_BYTES = 4096


class MultipartImportError(ValueError):
    def __init__(
        self,
        code: str,
        user_message: str,
        *,
        status_code: int,
        stage: str,
    ) -> None:
        self.code = code
        self.user_message = user_message
        self.status_code = status_code
        self.stage = stage
        super().__init__(user_message)


@dataclass(frozen=True)
class ParsedImport:
    project_name: str
    upload_bytes: int


class _MultipartImportParser:
    def __init__(self, destination: BinaryIO, max_import_bytes: int) -> None:
        self._destination = destination
        self._max_import_bytes = max_import_bytes
        self._header_field = bytearray()
        self._header_value = bytearray()
        self._headers: dict[bytes, bytes] = {}
        self._header_count = 0
        self._part_header_bytes = 0
        self._part_kind: Literal["project_name", "pack"] | None = None
        self._project_name = bytearray()
        self._upload_bytes = 0
        self._seen_project_name = False
        self._seen_pack = False
        self._part_complete = False
        self.ended = False

    @property
    def callbacks(self):
        return {
            "on_part_begin": self.on_part_begin,
            "on_part_data": self.on_part_data,
            "on_part_end": self.on_part_end,
            "on_header_field": self.on_header_field,
            "on_header_value": self.on_header_value,
            "on_header_end": self.on_header_end,
            "on_headers_finished": self.on_headers_finished,
            "on_end": self.on_end,
        }

    def on_part_begin(self) -> None:
        self._headers = {}
        self._header_count = 0
        self._part_header_bytes = 0
        self._part_kind = None
        self._part_complete = False

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        chunk = data[start:end]
        if len(self._header_field) + len(chunk) > MAX_MULTIPART_HEADER_FIELD_BYTES:
            raise _invalid_multipart()
        self._add_part_header_bytes(len(chunk))
        self._header_field.extend(chunk)

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        chunk = data[start:end]
        if len(self._header_value) + len(chunk) > MAX_MULTIPART_HEADER_VALUE_BYTES:
            raise _invalid_multipart()
        self._add_part_header_bytes(len(chunk))
        self._header_value.extend(chunk)

    def on_header_end(self) -> None:
        self._header_count += 1
        if self._header_count > MAX_MULTIPART_HEADERS_PER_PART:
            raise _invalid_multipart()
        name = bytes(self._header_field).lower()
        if not name or name in self._headers:
            raise _invalid_multipart()
        self._headers[name] = bytes(self._header_value)
        self._header_field.clear()
        self._header_value.clear()

    def _add_part_header_bytes(self, count: int) -> None:
        self._part_header_bytes += count
        if self._part_header_bytes > MAX_MULTIPART_PART_HEADER_BYTES:
            raise _invalid_multipart()

    def on_headers_finished(self) -> None:
        disposition, options = parse_options_header(
            self._headers.get(b"content-disposition")
        )
        if disposition != b"form-data" or b"name" not in options:
            raise _invalid_multipart()
        try:
            field_name = options[b"name"].decode("utf-8")
        except UnicodeDecodeError as error:
            raise _invalid_multipart() from error
        if field_name == "project_name" and b"filename" not in options:
            if self._seen_project_name:
                raise _invalid_multipart()
            self._seen_project_name = True
            self._part_kind = "project_name"
        elif field_name == "pack" and b"filename" in options:
            if self._seen_pack:
                raise _invalid_multipart()
            self._seen_pack = True
            self._part_kind = "pack"
        else:
            raise _invalid_multipart()

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        chunk = data[start:end]
        if self._part_kind == "project_name":
            self._project_name.extend(chunk)
            if len(self._project_name) > MAX_PROJECT_NAME_UTF8_BYTES:
                raise _invalid_project_name()
            return
        if self._part_kind != "pack":
            raise _invalid_multipart()
        self._upload_bytes += len(chunk)
        if self._upload_bytes > self._max_import_bytes:
            raise MultipartImportError(
                "IMPORT_TOO_LARGE",
                "上传的资源包超过允许大小",
                status_code=413,
                stage="uploading",
            )
        try:
            _write_upload_chunk(self._destination, chunk)
        except OSError as error:
            raise _storage_unavailable() from error

    def on_part_end(self) -> None:
        if self._part_kind is None:
            raise _invalid_multipart()
        self._part_complete = True

    def on_end(self) -> None:
        self.ended = True

    def result(self) -> ParsedImport:
        if (
            not self.ended
            or not self._part_complete
            or not self._seen_project_name
            or not self._seen_pack
            or self._upload_bytes == 0
        ):
            raise _invalid_multipart()
        try:
            project_name = self._project_name.decode("utf-8").strip()
        except UnicodeDecodeError as error:
            raise _invalid_project_name() from error
        if not project_name or len(project_name) > MAX_PROJECT_NAME_LENGTH:
            raise _invalid_project_name()
        return ParsedImport(
            project_name=project_name,
            upload_bytes=self._upload_bytes,
        )


async def parse_import_multipart(
    request: Request,
    destination: BinaryIO,
    max_import_bytes: int,
) -> ParsedImport:
    try:
        content_type, options = parse_options_header(
            request.headers.get("content-type")
        )
    except (TypeError, ValueError) as error:
        raise _invalid_multipart() from error
    if content_type != b"multipart/form-data":
        raise MultipartImportError(
            "INVALID_REQUEST",
            "请求格式无效；导入只接受项目名称和 ZIP 文件上传",
            status_code=422,
            stage="request_validation",
        )
    boundary = options.get(b"boundary")
    if not boundary or len(boundary) > MAX_MULTIPART_BOUNDARY_BYTES:
        raise _invalid_multipart()
    state = _MultipartImportParser(destination, max_import_bytes)
    try:
        parser = MultipartParser(
            boundary,
            state.callbacks,
            max_header_count=MAX_MULTIPART_HEADERS_PER_PART,
            max_header_size=MAX_MULTIPART_PART_HEADER_BYTES,
        )
        async for chunk in request.stream():
            parser.write(chunk)
        parser.finalize()
        return state.result()
    except MultipartImportError:
        raise
    except (FormParserError, MultipartParseError, OSError, ValueError) as error:
        raise _invalid_multipart() from error


def _invalid_multipart() -> MultipartImportError:
    return MultipartImportError(
        "INVALID_MULTIPART",
        "multipart 上传内容无效",
        status_code=400,
        stage="request_validation",
    )


def _invalid_project_name() -> MultipartImportError:
    return MultipartImportError(
        "INVALID_PROJECT_NAME",
        f"项目名称必须为 1 到 {MAX_PROJECT_NAME_LENGTH} 个字符",
        status_code=400,
        stage="importing",
    )


def _write_upload_chunk(destination: BinaryIO, chunk: bytes) -> None:
    written = destination.write(chunk)
    if written != len(chunk):
        raise OSError("Temporary upload write was incomplete")


def _storage_unavailable() -> MultipartImportError:
    return MultipartImportError(
        "PROJECT_STORAGE_UNAVAILABLE",
        "无法写入项目存储目录",
        status_code=500,
        stage="uploading",
    )
