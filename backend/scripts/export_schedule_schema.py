"""Export the generated Canonical Schedule v2.2 JSON Schema."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT / "package") not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT / "package"))

from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22  # noqa: E402

SCHEMA_PATH = (
    BACKEND_ROOT
    / "package"
    / "yuxi"
    / "schedule"
    / "contracts"
    / "schemas"
    / "canonical_schedule_v2_2.schema.json"
)


def main() -> None:
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(CanonicalScheduleV22.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    SCHEMA_PATH.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
