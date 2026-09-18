"""Project Canonical Schedule v2.8 into the shared Schedule domain."""

from yuxi.schedule.contracts.canonical_v2_8 import CanonicalScheduleV28
from yuxi.schedule.domain.models import ScheduleSnapshot
from yuxi.schedule.importers.canonical_v2_2 import _import_canonical_schedule


def import_canonical_schedule_v2_8(source: CanonicalScheduleV28) -> ScheduleSnapshot:
    return _import_canonical_schedule(source)
