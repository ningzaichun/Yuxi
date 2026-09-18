"""Adapter routing for versioned external Schedule documents."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from yuxi.schedule.contracts.canonical import CanonicalSchedule
from yuxi.schedule.contracts.import_v1 import ScheduleNormalizationReport


class UnsupportedScheduleImportVersionError(ValueError):
    """Raised when no adapter is registered for a source schema version."""


@dataclass(frozen=True, slots=True)
class ScheduleImportResult:
    source_document: dict[str, Any]
    canonical: CanonicalSchedule
    normalization_report: ScheduleNormalizationReport


class ScheduleImportAdapter(Protocol):
    schema_version: str
    adapter_id: str
    adapter_version: str

    def normalize(self, document: dict[str, Any]) -> ScheduleImportResult: ...


class ScheduleImportAdapterRegistry:
    def __init__(self, adapters: tuple[ScheduleImportAdapter, ...] = ()) -> None:
        self._adapters: dict[str, ScheduleImportAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ScheduleImportAdapter) -> None:
        if adapter.schema_version in self._adapters:
            raise ValueError(f"duplicate Schedule import adapter: {adapter.schema_version}")
        self._adapters[adapter.schema_version] = adapter

    def normalize(self, document: dict[str, Any]) -> ScheduleImportResult:
        schema_version = document.get("schema_version")
        if not isinstance(schema_version, str) or not schema_version:
            raise ValueError("document.schema_version must be a non-empty string")
        adapter = self._adapters.get(schema_version)
        if adapter is None:
            raise UnsupportedScheduleImportVersionError(f"unsupported Schedule import version: {schema_version}")
        return adapter.normalize(deepcopy(document))
