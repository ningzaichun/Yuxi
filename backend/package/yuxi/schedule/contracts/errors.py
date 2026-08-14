"""Normalize Pydantic validation failures into the Schedule API contract."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import ValidationError

from .audit import ScheduleErrorDetail, ScheduleFieldError

_ERROR_CODES = {
    "extra_forbidden": "UNKNOWN_FIELD",
    "list_type": "INVALID_ARRAY",
    "literal_error": "INVALID_ENUM",
    "missing": "REQUIRED_FIELD_MISSING",
    "timezone_aware": "INVALID_DATETIME",
    "too_long": "COLLECTION_LIMIT_EXCEEDED",
}


def validation_error_to_schedule_detail(error: ValidationError) -> ScheduleErrorDetail:
    """Return stable codes and JSON Pointers without leaking submitted values."""

    errors = [
        ScheduleFieldError(
            path=_json_pointer(item["loc"]),
            code=_error_code(str(item["type"])),
            message=str(item["msg"]),
        )
        for item in error.errors(include_url=False, include_context=False, include_input=False)
    ]
    return ScheduleErrorDetail(
        code="SCHEDULE_CONTRACT_INVALID",
        message="排期数据不符合 canonical_schedule_v2.2",
        errors=errors,
    )


def validation_error_to_schedule_import_detail(
    error: ValidationError,
    *,
    path_prefix: tuple[str | int, ...] = (),
) -> ScheduleErrorDetail:
    """Return import-boundary errors without exposing submitted source values."""

    errors = [
        ScheduleFieldError(
            path=_json_pointer((*path_prefix, *item["loc"])),
            code=_error_code(str(item["type"])),
            message=str(item["msg"]),
        )
        for item in error.errors(include_url=False, include_context=False, include_input=False)
    ]
    return ScheduleErrorDetail(
        code="SCHEDULE_IMPORT_CONTRACT_INVALID",
        message="排期来源数据不符合声明的导入格式",
        errors=errors,
    )


def _json_pointer(location: Iterable[str | int]) -> str:
    parts = [str(part).replace("~", "~0").replace("/", "~1") for part in location]
    return "/" + "/".join(parts) if parts else "/"


def _error_code(error_type: str) -> str:
    if error_type.startswith("datetime_"):
        return "INVALID_DATETIME"
    if error_type.startswith("value_error"):
        return "SEMANTIC_CONTRACT_INVALID"
    return _ERROR_CODES.get(error_type, "INVALID_FIELD")
