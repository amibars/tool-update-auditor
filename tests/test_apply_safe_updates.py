from __future__ import annotations

import subprocess

from apply_safe_updates import execute_plan
from update_audit import ClassifiedToolRecord


def _record(name: str) -> ClassifiedToolRecord:
    return ClassifiedToolRecord(
        name=name,
        manager="npm",
        current_version="1.0.0",
        latest_version="1.1.0",
        update_channel="npm-global",
        recommended_command=f"npm -g update {name}",
        auto_apply=True,
        age_gate_status="ready",
    )


def test_execute_plan_marks_dry_run_records_as_skipped() -> None:
    result = execute_plan([_record("openclaw")], execute=False)

    assert result["applied"] == []
    assert result["failed"] == []
    assert result["skipped"] == [
        {
            "name": "openclaw",
            "current_version": "1.0.0",
            "latest_version": "1.1.0",
            "command": "npm -g update openclaw",
            "reason": "dry-run",
        }
    ]


def test_execute_plan_splits_execute_results_into_applied_and_failed(monkeypatch) -> None:
    records = [_record("openclaw"), _record("pnpm")]
    codes = iter([0, 1])
    commands: list[list[str]] = []

    def fake_run(command: list[str], check: bool) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, next(codes))

    monkeypatch.setattr("apply_safe_updates.subprocess.run", fake_run)

    result = execute_plan(records, execute=True)

    assert commands == [
        ["npm", "-g", "update", "openclaw"],
        ["npm", "-g", "update", "pnpm"],
    ]
    assert [item["name"] for item in result["applied"]] == ["openclaw"]
    assert [item["name"] for item in result["failed"]] == ["pnpm"]
    assert result["failed"][0]["exit_code"] == 1
