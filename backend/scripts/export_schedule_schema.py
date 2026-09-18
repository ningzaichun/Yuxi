"""Export the generated Canonical Schedule JSON Schemas."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT / "package") not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT / "package"))

from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22  # noqa: E402
from yuxi.schedule.contracts.canonical_v2_3 import CanonicalScheduleV23  # noqa: E402
from yuxi.schedule.contracts.canonical_v2_4 import CanonicalScheduleV24  # noqa: E402
from yuxi.schedule.contracts.canonical_v2_5 import CanonicalScheduleV25  # noqa: E402
from yuxi.schedule.contracts.canonical_v2_6 import CanonicalScheduleV26  # noqa: E402
from yuxi.schedule.contracts.canonical_v2_7 import CanonicalScheduleV27  # noqa: E402
from yuxi.schedule.contracts.canonical_v2_8 import CanonicalScheduleV28  # noqa: E402
from yuxi.schedule.contracts.import_v1 import MicrosoftProjectInterchangeFormalV11  # noqa: E402

SCHEMA_ROOT = (
    BACKEND_ROOT
    / "package"
    / "yuxi"
    / "schedule"
    / "contracts"
    / "schemas"
)
SCHEMAS = {
    "canonical_schedule_v2_2.schema.json": CanonicalScheduleV22,
    "canonical_schedule_v2_3.schema.json": CanonicalScheduleV23,
    "canonical_schedule_v2_4.schema.json": CanonicalScheduleV24,
    "canonical_schedule_v2_5.schema.json": CanonicalScheduleV25,
    "canonical_schedule_v2_6.schema.json": CanonicalScheduleV26,
    "canonical_schedule_v2_7.schema.json": CanonicalScheduleV27,
    "canonical_schedule_v2_8.schema.json": CanonicalScheduleV28,
    "microsoft_project_interchange_v1_1.schema.json": MicrosoftProjectInterchangeFormalV11,
}


def main() -> None:
    SCHEMA_ROOT.mkdir(parents=True, exist_ok=True)
    for file_name, contract in SCHEMAS.items():
        content = json.dumps(contract.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        (SCHEMA_ROOT / file_name).write_text(content, encoding="utf-8")
        if file_name == "microsoft_project_interchange_v1_1.schema.json":
            bridge_schema_root = BACKEND_ROOT.parent / "tools" / "mpp-bridge" / "schemas"
            bridge_schema_root.mkdir(parents=True, exist_ok=True)
            (bridge_schema_root / file_name).write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
