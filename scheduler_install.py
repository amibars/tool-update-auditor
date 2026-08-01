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


def audit_task_command(base_dir: str, python_exe: str) -> str:
    return f'cmd.exe /c ""{python_exe}" "{base_dir}\\startup_runner.py""'


def weekly_audit_task_command(base_dir: str, python_exe: str) -> str:
    return f'cmd.exe /c ""{python_exe}" "{base_dir}\\startup_runner.py""'


def build_task_specs(base_dir: str, python_exe: str) -> list[TaskSpec]:
    return [
        TaskSpec(
            name="ToolUpdateAuditor-AuditOnLogon",
            trigger_kind="logon",
            command=audit_task_command(base_dir, python_exe),
            schedule_args=["/SC", "ONLOGON"],
            description="Run startup_runner on user logon so ready auto-updates apply in the same cycle.",
        ),
        TaskSpec(
            name="ToolUpdateAuditor-AuditWeekly",
            trigger_kind="weekly",
            command=weekly_audit_task_command(base_dir, python_exe),
            schedule_args=["/SC", "WEEKLY", "/D", "SUN", "/ST", "03:30"],
            description="Run an audit weekly. This task never applies updates by itself.",
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
    args = parser.parse_args()

    base_dir = str(Path(__file__).resolve().parent)
    python_exe = sys.executable
    specs = build_task_specs(base_dir, python_exe)

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
