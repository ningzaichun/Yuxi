"""Benchmark the real Interchange -> Canonical -> Audit -> CPM path."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from time import perf_counter_ns
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = BACKEND_ROOT / "package"
for import_root in (BACKEND_ROOT, PACKAGE_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from yuxi.schedule.audit.engine import audit_schedule  # noqa: E402
from yuxi.schedule.forward_engine import (  # noqa: E402
    calculate_minimal_forward_schedule,
    recalculation_profile_for,
)
from yuxi.schedule.importers import (  # noqa: E402
    build_default_schedule_import_registry,
    import_canonical_schedule,
)


def benchmark_interchange_document(
    document: dict[str, Any],
    *,
    iterations: int,
    max_p95_ms: float,
) -> dict[str, Any]:
    if iterations < 5:
        raise ValueError("iterations must be at least 5")
    if max_p95_ms <= 0:
        raise ValueError("max_p95_ms must be positive")

    registry = build_default_schedule_import_registry()
    source_hash = str((document.get("source") or {}).get("mpp_sha256") or "")
    timings = {name: [] for name in ("normalize", "audit", "cpm", "end_to_end")}
    final_result = None
    final_audit = None
    final_engine = None

    for iteration in range(iterations + 2):
        started = perf_counter_ns()
        result = registry.normalize(document)
        normalized = perf_counter_ns()
        execution = audit_schedule(
            import_canonical_schedule(result.canonical),
            schedule_snapshot_id=result.canonical.snapshot_id,
            audit_run_id="benchmark:real-interchange",
        )
        audited = perf_counter_ns()
        engine = calculate_minimal_forward_schedule(
            result.canonical,
            engine_profile_id=recalculation_profile_for(result.canonical),
        )
        completed = perf_counter_ns()
        if iteration >= 2:
            timings["normalize"].append((normalized - started) / 1_000_000)
            timings["audit"].append((audited - normalized) / 1_000_000)
            timings["cpm"].append((completed - audited) / 1_000_000)
            timings["end_to_end"].append((completed - started) / 1_000_000)
        final_result = result
        final_audit = execution
        final_engine = engine

    assert final_result is not None and final_audit is not None and final_engine is not None
    timing_summary = {name: _summarize(values) for name, values in timings.items()}
    p95_ms = timing_summary["end_to_end"]["p95"]
    return {
        "case_id": f"mpp:{source_hash[:12]}" if source_hash else "mpp:unknown",
        "source_schema_version": document.get("schema_version"),
        "adapter_id": final_result.normalization_report.adapter_id,
        "adapter_version": final_result.normalization_report.adapter_version,
        "canonical_schema_version": final_result.canonical.schema_version,
        "engine_profile_id": final_engine["engine_profile_id"],
        "engine_status": final_engine["status"],
        "tasks": len(final_result.canonical.tasks),
        "dependencies": len(final_result.canonical.dependencies),
        "calendars": len(final_result.canonical.calendars),
        "audit_findings": len(final_audit.findings),
        "iterations": iterations,
        "timings_ms": timing_summary,
        "gate": {
            "max_end_to_end_p95_ms": max_p95_ms,
            "passed": final_engine["status"] == "calculated" and p95_ms <= max_p95_ms,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark formal Microsoft Project Interchange inputs")
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--max-p95-ms", type=float, default=100.0)
    args = parser.parse_args(argv)

    results = [
        benchmark_interchange_document(
            json.loads(path.read_text(encoding="utf-8")),
            iterations=args.iterations,
            max_p95_ms=args.max_p95_ms,
        )
        for path in args.input
    ]
    report = {
        "benchmark": "schedule_real_interchange_v1",
        "case_count": len(results),
        "passed": all(result["gate"]["passed"] for result in results),
        "cases": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


def _summarize(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "p50": round(ordered[math.ceil(len(ordered) * 0.50) - 1], 3),
        "p95": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
        "max": round(ordered[-1], 3),
    }


if __name__ == "__main__":
    raise SystemExit(main())
