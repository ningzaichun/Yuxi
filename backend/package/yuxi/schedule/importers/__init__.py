"""Canonical Schedule importers."""

from yuxi.schedule.contracts.canonical import CanonicalSchedule
from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22
from yuxi.schedule.contracts.canonical_v2_3 import CanonicalScheduleV23
from yuxi.schedule.contracts.canonical_v2_4 import CanonicalScheduleV24
from yuxi.schedule.contracts.canonical_v2_5 import CanonicalScheduleV25
from yuxi.schedule.contracts.canonical_v2_6 import CanonicalScheduleV26
from yuxi.schedule.contracts.canonical_v2_7 import CanonicalScheduleV27
from yuxi.schedule.contracts.canonical_v2_8 import CanonicalScheduleV28
from yuxi.schedule.domain.models import ScheduleSnapshot

from .canonical_v2_2 import import_canonical_schedule_v2_2
from .canonical_v2_3 import import_canonical_schedule_v2_3
from .canonical_v2_4 import import_canonical_schedule_v2_4
from .canonical_v2_5 import import_canonical_schedule_v2_5
from .canonical_v2_6 import import_canonical_schedule_v2_6
from .canonical_v2_7 import import_canonical_schedule_v2_7
from .canonical_v2_8 import import_canonical_schedule_v2_8
from .microsoft_project_interchange_v1_1 import (
    MicrosoftProjectInterchangeMockV11Adapter,
    MicrosoftProjectInterchangeV11Adapter,
)
from .registry import (
    ScheduleImportAdapterRegistry,
    ScheduleImportResult,
    UnsupportedScheduleImportVersionError,
)


def build_default_schedule_import_registry() -> ScheduleImportAdapterRegistry:
    return ScheduleImportAdapterRegistry(
        (
            MicrosoftProjectInterchangeV11Adapter(),
            MicrosoftProjectInterchangeMockV11Adapter(),
        )
    )


def import_canonical_schedule(source: CanonicalSchedule) -> ScheduleSnapshot:
    if isinstance(source, CanonicalScheduleV28):
        return import_canonical_schedule_v2_8(source)
    if isinstance(source, CanonicalScheduleV27):
        return import_canonical_schedule_v2_7(source)
    if isinstance(source, CanonicalScheduleV26):
        return import_canonical_schedule_v2_6(source)
    if isinstance(source, CanonicalScheduleV25):
        return import_canonical_schedule_v2_5(source)
    if isinstance(source, CanonicalScheduleV24):
        return import_canonical_schedule_v2_4(source)
    if isinstance(source, CanonicalScheduleV23):
        return import_canonical_schedule_v2_3(source)
    if isinstance(source, CanonicalScheduleV22):
        return import_canonical_schedule_v2_2(source)
    raise ValueError(f"unsupported Canonical Schedule version: {source.schema_version}")


__all__ = [
    "MicrosoftProjectInterchangeV11Adapter",
    "MicrosoftProjectInterchangeMockV11Adapter",
    "ScheduleImportAdapterRegistry",
    "ScheduleImportResult",
    "UnsupportedScheduleImportVersionError",
    "build_default_schedule_import_registry",
    "import_canonical_schedule_v2_2",
    "import_canonical_schedule_v2_3",
    "import_canonical_schedule_v2_4",
    "import_canonical_schedule_v2_5",
    "import_canonical_schedule_v2_6",
    "import_canonical_schedule_v2_7",
    "import_canonical_schedule_v2_8",
    "import_canonical_schedule",
]
