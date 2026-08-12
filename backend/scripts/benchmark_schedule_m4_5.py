"""Measure the reproducible M4.5 Schedule audit and synchronous HTTP baselines."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import httpx
from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[1]
for path in (BACKEND_ROOT, BACKEND_ROOT / "package"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

load_dotenv(BACKEND_ROOT / ".env", override=False)
load_dotenv(BACKEND_ROOT / "test" / ".env.test", override=False)

from schedule_benchmark import build_schedule_benchmark_payload  # noqa: E402
from yuxi.schedule.audit.engine import audit_schedule  # noqa: E402
from yuxi.schedule.contracts.canonical_v2_2 import CanonicalScheduleV22  # noqa: E402
from yuxi.schedule.importers.canonical_v2_2 import import_canonical_schedule_v2_2  # noqa: E402
from yuxi.schedule.storage import SCHEDULE_BUCKET  # noqa: E402
from yuxi.storage.minio.client import get_minio_client  # noqa: E402


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _summary(values: list[float], budget_seconds: float) -> dict[str, Any]:
    return {
        "samples": len(values),
        "p50_seconds": round(_percentile(values, 0.50), 6),
        "p95_seconds": round(_percentile(values, 0.95), 6),
        "max_seconds": round(max(values), 6),
        "budget_seconds": budget_seconds,
        "passed": _percentile(values, 0.95) <= budget_seconds,
    }


def _measure_audit(payload: dict, runs: int) -> dict[str, Any]:
    contract = CanonicalScheduleV22.model_validate(payload)
    schedule = import_canonical_schedule_v2_2(contract)

    # One unmeasured call primes imports, Pydantic serializers, and Python caches.
    audit_schedule(schedule, schedule_snapshot_id="warmup", audit_run_id="warmup")
    durations = []
    for index in range(runs):
        started = time.perf_counter()
        audit_schedule(
            schedule,
            schedule_snapshot_id=f"memory-{index}",
            audit_run_id=f"memory-{index}",
        )
        durations.append(time.perf_counter() - started)
    return _summary(durations, 2.0)


async def _cleanup_http_snapshots(owner_uid: str, snapshots: list[tuple[str, str]]) -> None:
    for _, object_name in snapshots:
        await get_minio_client().adelete_file(SCHEDULE_BUCKET, object_name)
    if not snapshots:
        return
    dsn = os.environ["POSTGRES_URL"].replace("+asyncpg", "").replace("+psycopg", "")
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(
            "DELETE FROM schedule_snapshots WHERE owner_uid = $1 AND schedule_snapshot_id = ANY($2::varchar[])",
            owner_uid,
            [snapshot_id for snapshot_id, _ in snapshots],
        )
    finally:
        await connection.close()


async def _measure_http(payload: dict, runs: int, base_url: str) -> dict[str, Any]:
    username = os.getenv("TEST_USERNAME")
    password = os.getenv("TEST_PASSWORD")
    if not username or not password:
        raise RuntimeError("TEST_USERNAME and TEST_PASSWORD are required for the HTTP baseline")

    snapshots: list[tuple[str, str]] = []
    async with httpx.AsyncClient(base_url=base_url, timeout=120.0) as client:
        login = await client.post("/api/auth/token", data={"username": username, "password": password})
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        me = await client.get("/api/auth/me", headers=headers)
        me.raise_for_status()
        owner_uid = str(me.json()["uid"])
        durations = []
        try:
            # The HTTP warm-up is a real submission and is cleaned with measured samples.
            for index in range(runs + 1):
                token = uuid.uuid4().hex
                submission = {
                    "request_id": f"m4-5-benchmark-{token}",
                    "external_project_id": "m4-5-benchmark",
                    "external_snapshot_id": f"m4-5-benchmark-{token}",
                    "external_revision": "M4.5",
                    "snapshot": payload,
                }
                started = time.perf_counter()
                response = await client.post("/api/schedule/snapshots", json=submission, headers=headers)
                elapsed = time.perf_counter() - started
                response.raise_for_status()
                snapshot_id = str(response.json()["schedule_snapshot_id"])
                snapshots.append((snapshot_id, f"{owner_uid}/{snapshot_id}/snapshot.json"))
                if index:
                    durations.append(elapsed)
            return _summary(durations, 5.0)
        finally:
            await _cleanup_http_snapshots(owner_uid, snapshots)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--http-runs", type=int, default=20)
    parser.add_argument("--base-url", default=os.getenv("TEST_BASE_URL", "http://127.0.0.1:5050"))
    parser.add_argument("--skip-http", action="store_true")
    arguments = parser.parse_args()
    if arguments.runs < 1 or arguments.http_runs < 1:
        parser.error("run counts must be positive")

    payload = build_schedule_benchmark_payload()
    result: dict[str, Any] = {
        "environment": {
            "os": platform.platform(),
            "python": platform.python_version(),
            "processor": platform.processor() or platform.machine(),
            "logical_cpu_count": os.cpu_count(),
        },
        "sample": {
            "tasks": len(payload["tasks"]),
            "dependencies": len(payload["dependencies"]),
            "json_bytes": len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
            "warmup_runs": 1,
        },
        "memory_audit": _measure_audit(payload, arguments.runs),
    }
    if not arguments.skip_http:
        result["synchronous_http_submission"] = await _measure_http(
            payload,
            arguments.http_runs,
            arguments.base_url.rstrip("/"),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
