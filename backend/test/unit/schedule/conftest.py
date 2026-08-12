from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "data" / "schedule" / "schedule_v2_2_sanitized.json"


@pytest.fixture
def canonical_schedule_payload() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
