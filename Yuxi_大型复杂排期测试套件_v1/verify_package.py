#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8-sig"))
    failures = []
    for entry in manifest.get("root_files", []):
        path = ROOT / entry["path"]
        if not path.is_file():
            failures.append(f"missing: {path}")
            continue
        if path.stat().st_size != entry["size_bytes"]:
            failures.append(f"size mismatch: {path}")
        if digest(path) != entry["sha256"]:
            failures.append(f"sha256 mismatch: {path}")
    for case in manifest["cases"]:
        case_dir = ROOT / case["case_id"]
        for entry in case["files"]:
            path = case_dir / entry["path"]
            if not path.is_file():
                failures.append(f"missing: {path}")
                continue
            if path.stat().st_size != entry["size_bytes"]:
                failures.append(f"size mismatch: {path}")
            if digest(path) != entry["sha256"]:
                failures.append(f"sha256 mismatch: {path}")
        for name in ("input.json", "expected.json", "actual_template.json"):
            try:
                json.loads((case_dir / name).read_text(encoding="utf-8-sig"))
            except Exception as exc:
                failures.append(f"invalid JSON {case_dir / name}: {exc}")
    if failures:
        print("PACKAGE FAIL")
        for item in failures:
            print(f"- {item}")
        return 1
    print(f"PACKAGE PASS: {manifest['case_count']} cases and all case-file hashes match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
