from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.benchmark_schedule_interchange import benchmark_interchange_document

FIXTURE_PATH = (
    Path(__file__).resolve().parents[4]
    / "Microsoft_Project_水泵站排期_MOCK_v1.1"
    / "Microsoft_Project_水泵站排期_MOCK_v1.1.json"
)


def test_interchange_benchmark_measures_complete_read_only_path() -> None:
    document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    result = benchmark_interchange_document(document, iterations=5, max_p95_ms=10_000)

    assert result["canonical_schema_version"] == "canonical_schedule_v2.3"
    assert result["engine_status"] == "calculated"
    assert result["tasks"] == 21
    assert result["dependencies"] == 19
    assert result["iterations"] == 5
    assert result["gate"]["passed"] is True
    assert set(result["timings_ms"]) == {"normalize", "audit", "cpm", "end_to_end"}
    assert all(
        set(summary) == {"p50", "p95", "max"} and summary["max"] >= 0
        for summary in result["timings_ms"].values()
    )


@pytest.mark.parametrize(
    ("iterations", "max_p95_ms", "message"),
    [(4, 100, "iterations"), (5, 0, "max_p95_ms")],
)
def test_interchange_benchmark_rejects_invalid_gate_configuration(
    iterations: int,
    max_p95_ms: float,
    message: str,
) -> None:
    document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    with pytest.raises(ValueError, match=message):
        benchmark_interchange_document(
            document,
            iterations=iterations,
            max_p95_ms=max_p95_ms,
        )
