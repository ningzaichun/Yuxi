from __future__ import annotations

import copy
import json
from pathlib import Path

from scripts.verify_ms_project_v28_oracle import load_oracle, validate_oracle

ORACLE_PATH = (
    Path(__file__).resolve().parents[3]
    / "test"
    / "data"
    / "schedule"
    / "microsoft_project_v28_inactive_summary_dependency_oracle.json"
)


def test_v28_oracle_freezes_independently_recaptured_project_facts() -> None:
    oracle = load_oracle(ORACLE_PATH)

    assert validate_oracle(oracle) == []


def test_v28_oracle_rejects_save_reopen_drift() -> None:
    oracle = json.loads(ORACLE_PATH.read_text(encoding="utf-8"))
    oracle["external_observation"]["save_reopen_mismatches"] = ["task:3:start"]

    assert "SAVE_REOPEN_MISMATCH" in validate_oracle(oracle)


def test_v28_oracle_rejects_business_policy_drift() -> None:
    oracle = copy.deepcopy(load_oracle(ORACLE_PATH))
    oracle["business_decisions"]["inactive_rollup_policy"] = "INCLUDE_INACTIVE_DESCENDANTS"

    assert "BUSINESS_DECISIONS_MISMATCH" in validate_oracle(oracle)
