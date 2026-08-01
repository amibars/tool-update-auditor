from __future__ import annotations

import argparse
from dataclasses import dataclass
import getpass
from pathlib import Path
import subprocess
import sys


@dataclass(frozen=True)
class TaskSpec:
    name: str
    trigger_kind: str
    command: str
    schedule_args: list[str]
    description: str


def audit_task_command(
    base_dir: str,
    python_exe: str,
    *,
    apply_updates: bool = False,
) -> str:
    apply_args = " --apply-ready" if apply_updates else ""
    return f'cmd.exe /c ""{python_exe}" "{base_dir}\\startup_runner.py"{apply_args}"'


def weekly_audit_task_command(
    base_dir: str,
    python_exe: str,
    *,
    apply_updates: bool = False,
) -> str:
    return audit_task_command(base_dir, python_exe, apply_updates=apply_updates)


def build_task_specs(
    base_dir: str,
    python_exe: str,
    *,
    apply_updates: bool = False,
) -> list[TaskSpec]:
    mode_description = (
        "Audit and apply policy-approved updates that have passed the release-age gate."
        if apply_updates
        else "Run an audit only. This task never applies updates by itself."
    )
    return [
        TaskSpec(
            name="ToolUpdateAuditor-AuditOnLogon",
            trigger_kind="logon",
            command=audit_task_command(base_dir, python_exe, apply_updates=apply_updates),
            schedule_args=["/SC", "ONLOGON"],
            description=mode_description,
        ),
        TaskSpec(
            name="ToolUpdateAuditor-AuditWeekly",
            trigger_kind="weekly",
            command=weekly_audit_task_command(base_dir, python_exe, apply_updates=apply_updates),
            schedule_args=["/SC", "WEEKLY", "/D", "SUN", "/ST", "03:30"],
            description=mode_description,
        ),
    ]


def register_task(task: TaskSpec) -> None:
    current_user = getpass.getuser()
    subprocess.run(
        [
            "schtasks",
            "/Create",
            "/F",
            "/TN",
            task.name,
            *task.schedule_args,
            "/RL",
            "LIMITED",
            "/RU",
            current_user,
            "/TR",
            task.command,
        ],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Scheduled Tasks for ToolUpdateAuditor.")
    parser.add_argument("--execute", action="store_true", help="Register the tasks instead of printing them.")
    parser.add_argument(
        "--enable-auto-apply",
        action="store_true",
        help="Pass --apply-ready to the scheduled runner so eligible updates apply automatically.",
    )
    args = parser.parse_args()

    base_dir = str(Path(__file__).resolve().parent)
    python_exe = sys.executable
    specs = build_task_specs(base_dir, python_exe, apply_updates=args.enable_auto_apply)

    for spec in specs:
        print(spec.name)
        print(f"  trigger: {spec.trigger_kind}")
        print(f"  command: {spec.command}")
        if args.execute:
            register_task(spec)

    if not args.execute:
        print("")
        print("Dry run only. Re-run with --execute to register the scheduled tasks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
