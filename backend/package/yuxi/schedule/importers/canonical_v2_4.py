"""Project Canonical Schedule v2.4 into the shared Schedule domain."""

from yuxi.schedule.contracts.canonical_v2_4 import CanonicalScheduleV24
from yuxi.schedule.domain.models import ScheduleSnapshot
from yuxi.schedule.importers.canonical_v2_2 import _import_canonical_schedule


def import_canonical_schedule_v2_4(source: CanonicalScheduleV24) -> ScheduleSnapshot:
    return _import_canonical_schedule(source)
