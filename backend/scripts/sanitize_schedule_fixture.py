"""Create the deterministic, non-business Schedule v2.2 golden fixture."""

from __future__ import annotations

import hashlib
import json
import uuid
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPOSITORY_ROOT / "backend" / "test" / "data" / "schedule" / "schedule_v2_2_sanitized.json"
SANITIZE_NAMESPACE = uuid.UUID("b7205d95-4d0e-43a0-a156-2e2ebf8128fc")


def sanitize_snapshot(source: dict[str, Any]) -> dict[str, Any]:
    snapshot = json.loads(json.dumps(source, ensure_ascii=False))
    snapshot["snapshot_id"] = "snapshot:sanitized-v2.2"
    snapshot["source"]["file_name"] = "sanitized-schedule.mpp"
    snapshot["source"]["sha256"] = hashlib.sha256(b"sanitized-source-file").hexdigest()
    snapshot["project"]["project_id"] = "project:sanitized"
    snapshot["project"]["name"] = "脱敏示例项目"
    snapshot["project"]["source_file_name"] = "sanitized-schedule.mpp"
    snapshot["project"]["source_project_summary"]["name"] = "脱敏示例项目"

    for index, calendar in enumerate(snapshot["calendars"], start=1):
        calendar["name"] = f"脱敏日历-{index:02d}"

    for index, task in enumerate(snapshot["tasks"], start=1):
        task["name"] = f"脱敏任务-{index:03d}"
        task["source_guid"] = str(uuid.uuid5(SANITIZE_NAMESPACE, f"task-{index}"))
        task["notes"] = ""
        if task.get("source_resource_names_text") is not None:
            task["source_resource_names_text"] = "脱敏资源"

    for index, resource in enumerate(snapshot["resources"], start=1):
        resource["name"] = f"脱敏资源-{index:03d}"
        resource["source_guid"] = str(uuid.uuid5(SANITIZE_NAMESPACE, f"resource-{index}"))
        resource["group"] = None
        resource["code"] = None
        resource["material_label"] = None
        resource["notes"] = ""

    validation = snapshot["validation"]
    validation["snapshot_id"] = snapshot["snapshot_id"]
    validation["source_sha256"] = snapshot["source"]["sha256"]
    for index, issue in enumerate(validation["issues"], start=1):
        issue["message"] = f"脱敏来源校验信息-{index:02d}"

    assessment = validation["source_vs_conversion_assessment"]
    for index, defect in enumerate(assessment["conversion_defects_fixed_in_v2_2"], start=1):
        defect["source"] = f"脱敏来源说明-{index:02d}"
        defect["previous_behavior"] = f"脱敏旧行为-{index:02d}"
        defect["corrected_behavior"] = f"脱敏修正行为-{index:02d}"
    return snapshot


def main() -> None:
    parser = ArgumentParser(description="从私有 Schedule Snapshot 生成可提交的脱敏回归 Fixture")
    parser.add_argument("source", type=Path, help="私有 canonical_schedule_v2.2 JSON 路径")
    parser.add_argument("--output", type=Path, default=FIXTURE_PATH, help="脱敏 Fixture 输出路径")
    args = parser.parse_args()
    source = json.loads(args.source.read_text(encoding="utf-8"))
    fixture = sanitize_snapshot(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
