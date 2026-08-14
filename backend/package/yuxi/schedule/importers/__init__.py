"""Canonical Schedule importers."""

from .canonical_v2_2 import import_canonical_schedule_v2_2
from .microsoft_project_interchange_v1_1 import MicrosoftProjectInterchangeV11Adapter
from .registry import (
    ScheduleImportAdapterRegistry,
    ScheduleImportResult,
    UnsupportedScheduleImportVersionError,
)


def build_default_schedule_import_registry() -> ScheduleImportAdapterRegistry:
    return ScheduleImportAdapterRegistry((MicrosoftProjectInterchangeV11Adapter(),))


__all__ = [
    "MicrosoftProjectInterchangeV11Adapter",
    "ScheduleImportAdapterRegistry",
    "ScheduleImportResult",
    "UnsupportedScheduleImportVersionError",
    "build_default_schedule_import_registry",
    "import_canonical_schedule_v2_2",
]
