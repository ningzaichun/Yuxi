"""Rebuild the frozen large Schedule suite from repository-owned evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
PACKAGE_ROOT = BACKEND_ROOT / "package"
for import_root in (BACKEND_ROOT, PACKAGE_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from test.support.schedule_suite import verify_suite_package  # noqa: E402

DEFAULT_SOURCE_ROOT = REPOSITORY_ROOT / "Yuxi_大型复杂排期测试套件_v1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild the frozen large Schedule suite byte-for-byte without external generator paths"
    )
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)

    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    if output_root == source_root or source_root in output_root.parents:
        parser.error("--output-root must be outside --source-root")
    if output_root.exists():
        parser.error("--output-root must not already exist")

    verify_suite_package(source_root)
    manifest = json.loads((source_root / "manifest.json").read_text(encoding="utf-8"))
    relative_paths = [Path("manifest.json")]
    relative_paths.extend(Path(entry["path"]) for entry in manifest["root_files"])
    relative_paths.extend(
        Path(case["case_id"]) / entry["path"]
        for case in manifest["cases"]
        for entry in case["files"]
    )

    for relative_path in relative_paths:
        target = output_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source_root / relative_path).read_bytes())

    verify_suite_package(output_root)
    print(
        json.dumps(
            {
                "output": str(output_root),
                "suite_id": manifest["suite_id"],
                "files": len(relative_paths),
                "rebuild_mode": "REPOSITORY_FROZEN_EVIDENCE_BYTE_COPY",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
