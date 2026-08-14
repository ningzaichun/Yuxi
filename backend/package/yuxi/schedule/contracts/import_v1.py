"""Versioned external Schedule import boundary contracts."""

from __future__ import annotations

from datetime import time
from typing import Annotated, Any, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

from .canonical_v2_2 import MAX_DEPENDENCIES, MAX_TASKS

NonEmptyString = Annotated[StrictStr, Field(min_length=1)]
Sha256String = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
PositiveInteger = Annotated[StrictInt, Field(gt=0)]
NonNegativeInteger = Annotated[StrictInt, Field(ge=0)]


class ScheduleImportEnvelope(BaseModel):
    """Stable import envelope; source-specific validation is delegated by schema version."""

    model_config = ConfigDict(extra="forbid")

    request_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    external_project_id: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    external_snapshot_id: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    external_revision: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    document: dict[str, Any]


class ImportDocumentModel(BaseModel):
    """Allow source extensions while keeping all declared fields typed."""

    model_config = ConfigDict(extra="allow")


class MicrosoftProjectImportSource(ImportDocumentModel):
    format: Literal["MPP"]
    file_name: NonEmptyString
    microsoft_project_version: NonEmptyString
    extraction_method: NonEmptyString
    extracted_at: AwareDatetime
    timezone: NonEmptyString
    mpp_sha256: Sha256String
    opened_after_save: StrictBool
    project_recalculated_after_reopen: StrictBool


class MicrosoftProjectImportSemantics(ImportDocumentModel):
    timezone: NonEmptyString
    duration_unit: Literal["working_minute"]
    lag_unit: Literal["working_minute"]
    lag_calendar_policy: Literal["SUCCESSOR_TASK_CALENDAR"]
    task_calendar_resolution: NonEmptyString


class MicrosoftProjectImportProject(ImportDocumentModel):
    project_id: NonEmptyString
    name: NonEmptyString
    planned_start: AwareDatetime
    planned_finish: AwareDatetime
    default_calendar_id: NonEmptyString
    default_daily_work_minutes: PositiveInteger
    default_weekly_work_minutes: PositiveInteger

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        if self.planned_finish < self.planned_start:
            raise ValueError("project.planned_finish must not be before planned_start")
        return self


class MicrosoftProjectImportInterval(ImportDocumentModel):
    start: time
    finish: time

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.start >= self.finish:
            raise ValueError("calendar interval finish must be after start")
        return self


class MicrosoftProjectImportWeekDay(ImportDocumentModel):
    day: Literal["SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY"]
    working: StrictBool
    intervals: list[MicrosoftProjectImportInterval]

    @model_validator(mode="after")
    def validate_working_intervals(self) -> Self:
        if self.working != bool(self.intervals):
            raise ValueError("working day must have intervals and non-working day must not")
        previous_finish: time | None = None
        for interval in self.intervals:
            if previous_finish is not None and interval.start < previous_finish:
                raise ValueError("calendar intervals must not overlap")
            previous_finish = interval.finish
        return self


class MicrosoftProjectImportCalendar(ImportDocumentModel):
    calendar_id: NonEmptyString
    name: NonEmptyString
    week_days: Annotated[list[MicrosoftProjectImportWeekDay], Field(min_length=7, max_length=7)]
    exceptions: list[dict[str, Any]]

    @model_validator(mode="after")
    def validate_week(self) -> Self:
        if len({day.day for day in self.week_days}) != 7:
            raise ValueError("calendar.week_days must contain each weekday exactly once")
        return self


class MicrosoftProjectImportTask(ImportDocumentModel):
    task_id: NonEmptyString
    source_id: PositiveInteger
    source_unique_id: PositiveInteger
    parent_task_id: NonEmptyString | None
    wbs: NonEmptyString
    outline_level: PositiveInteger
    name: NonEmptyString
    task_type: Literal["TASK", "SUMMARY", "MILESTONE"]
    scheduling_mode: Literal["AUTO", "MANUAL"]
    duration_minutes: NonNegativeInteger | None
    project_rollup_duration_minutes: NonNegativeInteger
    start: AwareDatetime
    finish: AwareDatetime
    percent_complete: Annotated[StrictInt, Field(ge=0, le=100)]
    constraint_type_code: Literal[0, 4, 6]
    constraint_date: AwareDatetime | None
    deadline: AwareDatetime | None
    calendar_id: NonEmptyString

    @model_validator(mode="after")
    def validate_task_semantics(self) -> Self:
        if self.finish < self.start:
            raise ValueError(f"task {self.task_id} finish must not be before start")
        if self.task_type == "SUMMARY" and self.duration_minutes is not None:
            raise ValueError(f"summary task {self.task_id} duration_minutes must be null")
        if self.task_type != "SUMMARY" and self.duration_minutes is None:
            raise ValueError(f"activity task {self.task_id} requires duration_minutes")
        if self.task_type == "MILESTONE" and self.duration_minutes != 0:
            raise ValueError(f"milestone task {self.task_id} must have zero duration")
        if self.constraint_type_code == 0 and self.constraint_date is not None:
            raise ValueError(f"ASAP task {self.task_id} must not have constraint_date")
        if self.constraint_type_code in {4, 6} and self.constraint_date is None:
            raise ValueError(f"constrained task {self.task_id} requires constraint_date")
        return self


class MicrosoftProjectImportDependency(ImportDocumentModel):
    dependency_id: NonEmptyString
    predecessor_task_id: NonEmptyString
    successor_task_id: NonEmptyString
    type: Literal["FS", "SS", "FF", "SF"]
    source_type_code: StrictInt
    lag_minutes: StrictInt
    lag_calendar_policy: Literal["SUCCESSOR_TASK_CALENDAR"]


class MicrosoftProjectInterchangeV11(ImportDocumentModel):
    """Typed boundary for the current Microsoft Project interchange artifact."""

    schema_version: Literal["microsoft_project_interchange_mock_v1.1"]
    artifact_version: NonEmptyString | None = None
    data_classification: NonEmptyString | None = None
    source: MicrosoftProjectImportSource
    semantics: MicrosoftProjectImportSemantics
    project: MicrosoftProjectImportProject
    statistics: dict[str, Any] | None = None
    capabilities: dict[str, Any] | None = None
    calendars: list[MicrosoftProjectImportCalendar]
    tasks: Annotated[list[MicrosoftProjectImportTask], Field(min_length=1, max_length=MAX_TASKS)]
    dependencies: Annotated[list[MicrosoftProjectImportDependency], Field(max_length=MAX_DEPENDENCIES)]
    resources: list[dict[str, Any]] = Field(default_factory=list)
    assignments: list[dict[str, Any]] = Field(default_factory=list)
    validation: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_identity_and_references(self) -> Self:
        if self.source.timezone != self.semantics.timezone:
            raise ValueError("source.timezone and semantics.timezone must match")

        calendar_ids = _unique_values("calendar_id", self.calendars)
        task_ids = _unique_values("task_id", self.tasks)
        _unique_values("dependency_id", self.dependencies)
        if self.project.default_calendar_id not in calendar_ids:
            raise ValueError("project.default_calendar_id references an unknown calendar")

        parents = {task.task_id: task.parent_task_id for task in self.tasks}
        for task in self.tasks:
            if task.parent_task_id is not None and task.parent_task_id not in task_ids:
                raise ValueError(f"task {task.task_id} references an unknown parent task")
            if task.calendar_id not in calendar_ids:
                raise ValueError(f"task {task.task_id} references an unknown calendar")
        _validate_parent_hierarchy(parents)

        for dependency in self.dependencies:
            if dependency.predecessor_task_id not in task_ids or dependency.successor_task_id not in task_ids:
                raise ValueError(f"dependency {dependency.dependency_id} references an unknown task")
        return self


class FieldDisposition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: NonEmptyString
    reason_code: NonEmptyString


class UnsupportedSemantic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: NonEmptyString
    object_refs: list[StrictStr] = Field(default_factory=list)


class ScheduleNormalizationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["schedule_normalization_report_v1"]
    source_schema_version: NonEmptyString
    adapter_id: NonEmptyString
    adapter_version: NonEmptyString
    preserved_fields: list[StrictStr]
    ignored_for_audit: list[FieldDisposition]
    ignored_for_calculation: list[FieldDisposition]
    unsupported_semantics: list[UnsupportedSemantic]


def collect_preserved_field_paths(value: Any, path: str = "") -> list[str]:
    """Collect JSON Pointers for fields accepted through Pydantic's extra storage."""
    paths: list[str] = []
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            child = getattr(value, name)
            paths.extend(collect_preserved_field_paths(child, f"{path}/{_pointer_part(name)}"))
        for name, child in (value.model_extra or {}).items():
            extra_path = f"{path}/{_pointer_part(name)}"
            paths.append(extra_path)
            paths.extend(collect_preserved_field_paths(child, extra_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            paths.extend(collect_preserved_field_paths(child, f"{path}/{index}"))
    return paths


def _unique_values(field_name: str, items: list[Any]) -> set[str]:
    values = [getattr(item, field_name) for item in items]
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {field_name}")
    return set(values)


def _validate_parent_hierarchy(parents: dict[str, str | None]) -> None:
    completed: set[str] = set()
    for task_id in parents:
        visiting: set[str] = set()
        current: str | None = task_id
        while current is not None and current not in completed:
            if current in visiting:
                raise ValueError(f"task parent hierarchy contains a cycle at {task_id}")
            visiting.add(current)
            current = parents[current]
        completed.update(visiting)


def _pointer_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")
