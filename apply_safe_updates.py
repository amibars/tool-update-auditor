from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess

from update_audit import ClassifiedToolRecord, build_safe_update_plan

RESULT_FILE_NAME = "apply-safe-updates-last-run.json"


def load_records(json_path: Path) -> list[ClassifiedToolRecord]:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    return [ClassifiedToolRecord(**item) for item in payload]


def _record_payload(record: ClassifiedToolRecord) -> dict:
    return {
        "name": record.name,
        "current_version": record.current_version,
        "latest_version": record.latest_version,
        "command": record.recommended_command,
    }


def execute_plan(plan: list[ClassifiedToolRecord], *, execute: bool) -> dict:
    result = {
        "planned_count": len(plan),
        "applied": [],
        "failed": [],
        "skipped": [],
    }
    for record in plan:
        item = _record_payload(record)
        if not execute:
            result["skipped"].append({**item, "reason": "dry-run"})
            continue
        completed = subprocess.run(
            shlex.split(record.recommended_command, posix=False),
            check=False,
        )
        if completed.returncode == 0:
            result["applied"].append(item)
        else:
            result["failed"].append({**item, "exit_code": completed.returncode})
    return result


def write_result(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan or apply safe tool updates from the audit JSON.")
    parser.add_argument("--execute", action="store_true", help="Run the selected update commands.")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    records = load_records(base_dir / "artifacts" / "tool-update-audit.json")
    plan = build_safe_update_plan(records)
    result_path = base_dir / "artifacts" / RESULT_FILE_NAME

    if not plan:
        write_result(
            result_path,
            {
                "planned_count": 0,
                "applied": [],
                "failed": [],
                "skipped": [],
            },
        )
        print("No safe updates pending.")
        return 0

    result = execute_plan(plan, execute=args.execute)
    write_result(result_path, result)

    for record in plan:
        print(f"{record.name}: {record.current_version} -> {record.latest_version}")
        print(f"  {record.recommended_command}")

    if not args.execute:
        print("")
        print("Dry run only. Re-run with --execute to apply the planned updates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
