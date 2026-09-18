from __future__ import annotations

import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    errors: list[str] = []
    if not MANIFEST.is_file():
        print("FAILED: manifest.json missing")
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    listed = {item["path"] for item in manifest["files"]}
    actual = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file() and path.name != "manifest.json" and "__pycache__" not in path.parts
    }
    if listed != actual:
        errors.append(f"file set mismatch: missing={sorted(listed-actual)}, unexpected={sorted(actual-listed)}")
    for item in manifest["files"]:
        path = ROOT / item["path"]
        if not path.is_file():
            errors.append(f"missing: {item['path']}")
            continue
        if path.stat().st_size != item["size"]:
            errors.append(f"size mismatch: {item['path']}")
        if sha256(path) != item["sha256"]:
            errors.append(f"sha256 mismatch: {item['path']}")
    for name in ("A_road_drainage_clean.mpp", "B_substation_clean.mpp"):
        path = ROOT / name
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"invalid MPP: {name}")
        if len(str(path.resolve())) >= 259:
            errors.append(f"MPP path too long: {name}")
    for name in ("A_validation_report.json", "B_validation_report.json"):
        report = json.loads((ROOT / name).read_text(encoding="utf-8"))
        if report["status"] != "PASS" or report["failed_checks"]:
            errors.append(f"acceptance report failed: {name}")
        if report["counts"]["business_resources"] != 0 or report["counts"]["business_assignments"] != 0:
            errors.append(f"business resources or assignments found: {name}")
    summary = json.loads((ROOT / "validation_summary.json").read_text(encoding="utf-8"))
    if summary["overall_status"] != "PASS":
        errors.append("validation_summary overall_status is not PASS")
    for path in ROOT.rglob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))
    for path in ROOT.rglob("*.xml"):
        root = ET.parse(path).getroot()
        if root.tag != "{http://schemas.microsoft.com/project}Project":
            errors.append(f"invalid MSPDI root: {path.relative_to(ROOT)}")
    if errors:
        print(f"PACKAGE VERIFICATION FAILED ({len(errors)} errors)")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"PACKAGE VERIFICATION PASSED: {len(manifest['files'])} hashed files, 2 MPP files, 2 acceptance reports")
    return 0


if __name__ == "__main__":
    sys.exit(main())
