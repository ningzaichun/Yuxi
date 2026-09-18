"""Project Canonical Schedule v2.3 into the shared Schedule domain."""

from yuxi.schedule.contracts.canonical_v2_3 import CanonicalScheduleV23
from yuxi.schedule.domain.models import ScheduleSnapshot
from yuxi.schedule.importers.canonical_v2_2 import _import_canonical_schedule


def import_canonical_schedule_v2_3(source: CanonicalScheduleV23) -> ScheduleSnapshot:
    return _import_canonical_schedule(source)
