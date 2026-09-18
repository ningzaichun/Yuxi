"""Project Canonical Schedule v2.6 into the shared Schedule domain."""

from yuxi.schedule.contracts.canonical_v2_6 import CanonicalScheduleV26
from yuxi.schedule.domain.models import ScheduleSnapshot
from yuxi.schedule.importers.canonical_v2_2 import _import_canonical_schedule


def import_canonical_schedule_v2_6(source: CanonicalScheduleV26) -> ScheduleSnapshot:
    return _import_canonical_schedule(source)
