from __future__ import annotations

import json
from pathlib import Path

import pytest

from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22
from yuxi.schedule.contracts.canonical_v2_3 import CanonicalScheduleV23
from yuxi.schedule.contracts.canonical_v2_4 import CanonicalScheduleV24
from yuxi.schedule.contracts.canonical_v2_5 import CanonicalScheduleV25
from yuxi.schedule.contracts.canonical_v2_6 import CanonicalScheduleV26
from yuxi.schedule.contracts.canonical_v2_7 import CanonicalScheduleV27
from yuxi.schedule.contracts.canonical_v2_8 import CanonicalScheduleV28
from yuxi.schedule.contracts.import_v1 import MicrosoftProjectInterchangeFormalV11

SCHEMA_ROOT = (
    Path(__file__).resolve().parents[3]
    / "package"
    / "yuxi"
    / "schedule"
    / "contracts"
    / "schemas"
)


@pytest.mark.parametrize(
    ("file_name", "contract"),
    [
        ("canonical_schedule_v2_2.schema.json", CanonicalScheduleV22),
        ("canonical_schedule_v2_3.schema.json", CanonicalScheduleV23),
        ("canonical_schedule_v2_4.schema.json", CanonicalScheduleV24),
        ("canonical_schedule_v2_5.schema.json", CanonicalScheduleV25),
        ("canonical_schedule_v2_6.schema.json", CanonicalScheduleV26),
        ("canonical_schedule_v2_7.schema.json", CanonicalScheduleV27),
        ("canonical_schedule_v2_8.schema.json", CanonicalScheduleV28),
        ("microsoft_project_interchange_v1_1.schema.json", MicrosoftProjectInterchangeFormalV11),
    ],
)
def test_committed_schema_matches_generated_contract(file_name, contract) -> None:
    committed = json.loads((SCHEMA_ROOT / file_name).read_text(encoding="utf-8"))

    assert committed == contract.model_json_schema()
