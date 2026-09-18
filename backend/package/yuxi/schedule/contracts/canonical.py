"""Version-discriminated Canonical Schedule parsing."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, TypeAdapter

from .canonical_v2_2 import CanonicalScheduleV22
from .canonical_v2_3 import CanonicalScheduleV23
from .canonical_v2_4 import CanonicalScheduleV24
from .canonical_v2_5 import CanonicalScheduleV25
from .canonical_v2_6 import CanonicalScheduleV26
from .canonical_v2_7 import CanonicalScheduleV27
from .canonical_v2_8 import CanonicalScheduleV28

CanonicalSchedule = Annotated[
    CanonicalScheduleV22
    | CanonicalScheduleV23
    | CanonicalScheduleV24
    | CanonicalScheduleV25
    | CanonicalScheduleV26
    | CanonicalScheduleV27
    | CanonicalScheduleV28,
    Field(discriminator="schema_version"),
]
_CANONICAL_ADAPTER = TypeAdapter(CanonicalSchedule)


def parse_canonical_schedule(
    value: Any,
) -> (
    CanonicalScheduleV22
    | CanonicalScheduleV23
    | CanonicalScheduleV24
    | CanonicalScheduleV25
    | CanonicalScheduleV26
    | CanonicalScheduleV27
    | CanonicalScheduleV28
):
    return _CANONICAL_ADAPTER.validate_python(value)
