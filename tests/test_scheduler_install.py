from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import startup_runner
from install_startup_runner import choose_python_launcher, launcher_content, resolve_windows_user_home
from scheduler_install import (
    audit_task_command,
    build_task_specs,
    weekly_audit_task_command,
)
from startup_runner import (
    acquire_startup_lock,
    build_auto_update_queue,
    collect_changed_updates,
    has_ready_auto_updates,
    lock_is_stale,
    load_apply_result,
    load_updates_only,
    render_last_run_report,
    resolve_run_status,
    run_startup_cycle,
    should_open_report,
    should_run_weekly_updates,
)


def test_audit_task_command_runs_startup_runner() -> None:
    command = audit_task_command(r"C:\Users\example\ToolUpdateAuditor", r"C:\Python312\python.exe")

    assert command.startswith('cmd.exe /c "')
    assert "startup_runner.py" in command
    assert "update_audit.py" not in command
    assert "render_updates_only.py" not in command
    assert "--execute" not in command


def test_weekly_audit_task_command_runs_startup_runner() -> None:
    command = weekly_audit_task_command(r"C:\Users\example\ToolUpdateAuditor", r"C:\Python312\python.exe")

    assert command.startswith('cmd.exe /c "')
    assert "startup_runner.py" in command
    assert "apply_safe_updates.py" not in command


def test_build_task_specs_has_logon_and_weekly_tasks() -> None:
    specs = build_task_specs(r"C:\Users\example\ToolUpdateAuditor", r"C:\Python312\python.exe")

    assert [spec.name for spec in specs] == [
        "ToolUpdateAuditor-AuditOnLogon",
        "ToolUpdateAuditor-AuditWeekly",
    ]
    assert specs[0].trigger_kind == "logon"
    assert specs[1].trigger_kind == "weekly"
    assert "startup_runner.py" in specs[0].command
    assert "startup_runner.py" in specs[1].command


def test_build_task_specs_enables_auto_apply_only_when_requested() -> None:
    specs = build_task_specs(
        r"C:\Users\example\ToolUpdateAuditor",
        r"C:\Python312\python.exe",
        apply_updates=True,
    )

    assert all("--apply-ready" in spec.command for spec in specs)
    assert all("release-age gate" in spec.description for spec in specs)


def test_should_run_weekly_updates_when_state_missing() -> None:
    assert should_run_weekly_updates(None, now=datetime(2026, 3, 22, tzinfo=timezone.utc)) is True


def test_should_run_weekly_updates_after_seven_days() -> None:
    now = datetime(2026, 3, 22, tzinfo=timezone.utc)
    last_run = now - timedelta(days=8)

    assert should_run_weekly_updates(last_run, now=now) is True


def test_should_not_run_weekly_updates_before_seven_days() -> None:
    now = datetime(2026, 3, 22, tzinfo=timezone.utc)
    last_run = now - timedelta(days=6, hours=23)

    assert should_run_weekly_updates(last_run, now=now) is False


def test_has_ready_auto_updates_detects_ready_items() -> None:
    assert has_ready_auto_updates(
        [
            {"name": "openclaw", "auto_apply": True, "age_gate_status": "deferred"},
            {"name": "pnpm", "auto_apply": True, "age_gate_status": "ready"},
        ]
    ) is True


def test_has_ready_auto_updates_ignores_non_auto_apply_items() -> None:
    assert has_ready_auto_updates(
        [
            {"name": "@anthropic-ai/claude-code", "auto_apply": False, "age_gate_status": "ready"},
            {"name": "GitHub CLI", "auto_apply": False, "age_gate_status": "unknown"},
        ]
    ) is False


def test_collect_changed_updates_detects_new_and_applied_items() -> None:
    previous = {
        "updates_pending": [
            {"name": "pnpm", "current_version": "10.30.1", "latest_version": "10.32.1"},
        ]
    }
    current = [
        {"name": "pnpm", "current_version": "10.30.1", "latest_version": "10.32.1"},
        {"name": "rtk", "current_version": "0.29.0", "latest_version": "0.31.0"},
    ]

    changed = collect_changed_updates(previous, current)

    assert changed == ["rtk"]


def test_should_open_report_when_updates_found() -> None:
    assert should_open_report(changed_updates=["rtk"], updates_applied=False) is True


def test_should_open_report_when_updates_applied() -> None:
    assert should_open_report(changed_updates=[], updates_applied=True) is True


def test_should_not_open_report_without_changes() -> None:
    assert should_open_report(changed_updates=[], updates_applied=False) is False


def test_should_open_report_when_auto_update_failed() -> None:
    assert should_open_report(changed_updates=[], updates_applied=False, failed_updates=["openclaw"]) is True


def test_render_last_run_report_includes_changed_updates() -> None:
    report = render_last_run_report(
        {
            "ran_at": "2026-03-22T21:00:00+00:00",
            "updates_applied": True,
            "updates_pending_count": 3,
            "changed_updates": ["rtk", "pnpm"],
            "updates_pending": [
                {"name": "rtk"},
                {"name": "pnpm"},
                {"name": "Node.js"},
            ],
            "report_path": "C:/Users/example/ToolUpdateAuditor/artifacts/startup-runner-last-run.md",
        }
    )

    assert "# Последний автозапуск ToolUpdateAuditor" in report
    assert "обновления применялись: да" in report.lower()
    assert "rtk" in report
    assert "pnpm" in report


def test_render_last_run_report_includes_pending_updates_even_without_new_items() -> None:
    report = render_last_run_report(
        {
            "ran_at": "2026-03-22T21:00:00+00:00",
            "updates_applied": False,
            "updates_pending_count": 2,
            "changed_updates": [],
            "auto_update_queue": [
                {
                    "name": "OpenCode",
                    "current_version": "1.4.0",
                    "latest_version": "1.4.3",
                    "age_gate_status": "deferred",
                    "eligible_after": "2026-03-24T09:00:00Z",
                },
                {
                    "name": "Linear 1.28.12",
                    "current_version": "1.28.12",
                    "latest_version": "1.28.13",
                    "age_gate_status": "ready",
                    "eligible_after": "2026-03-20T09:00:00Z",
                },
            ],
            "report_path": "C:/Users/example/ToolUpdateAuditor/artifacts/startup-runner-last-run.md",
        },
        now=datetime(2026, 3, 22, 21, 0, tzinfo=timezone.utc),
    )

    assert "OpenCode" in report
    assert "Linear 1.28.12" in report
    assert "## Очередь автообновлений" in report


def test_render_last_run_report_includes_applied_updates_and_auto_queue_timing() -> None:
    report = render_last_run_report(
        {
            "ran_at": "2026-04-11T07:00:00+00:00",
            "updates_applied": True,
            "changed_updates": ["@anthropic-ai/claude-code"],
            "updates_pending_count": 2,
            "applied_updates": [
                {
                    "name": "openclaw",
                    "current_version": "2026.3.13",
                    "latest_version": "2026.3.28",
                }
            ],
            "auto_update_queue": [
                {
                    "name": "@anthropic-ai/claude-code",
                    "current_version": "2.1.92",
                    "latest_version": "2.1.101",
                    "age_gate_status": "deferred",
                    "eligible_after": "2026-04-13T18:00:00Z",
                },
                {
                    "name": "pnpm",
                    "current_version": "10.30.1",
                    "latest_version": "10.32.1",
                    "age_gate_status": "ready",
                    "eligible_after": "2026-04-10T00:00:00Z",
                },
            ],
            "report_path": "C:/Users/example/ToolUpdateAuditor/artifacts/startup-runner-last-run.md",
        },
        now=datetime(2026, 4, 11, 7, 0, tzinfo=timezone.utc),
    )

    assert "## Применено в этот запуск" in report
    assert "openclaw" in report
    assert "## Очередь автообновлений" in report
    assert "ready now" in report
    assert "2d 11h" in report


def test_load_updates_only_preserves_age_gate_fields_for_queue(tmp_path) -> None:
    json_path = tmp_path / "tool-update-audit.json"
    json_path.write_text(
        json.dumps(
            [
                {
                    "name": "openclaw",
                    "current_version": "2026.3.13",
                    "latest_version": "2026.3.28",
                    "manager": "npm",
                    "update_channel": "npm-global",
                    "auto_apply": True,
                    "release_published_at": "2026-04-01T10:15:00Z",
                    "eligible_after": "2026-04-04T10:15:00Z",
                    "age_gate_status": "ready",
                    "age_gate_reason": None,
                }
            ],
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )

    updates = load_updates_only(json_path)

    assert updates == [
        {
            "name": "openclaw",
            "current_version": "2026.3.13",
            "latest_version": "2026.3.28",
            "manager": "npm",
            "channel": "npm-global",
            "auto_apply": True,
            "release_published_at": "2026-04-01T10:15:00Z",
            "eligible_after": "2026-04-04T10:15:00Z",
            "age_gate_status": "ready",
            "age_gate_reason": None,
        }
    ]


def test_build_auto_update_queue_only_keeps_auto_apply_updates() -> None:
    queue = build_auto_update_queue(
        [
            {"name": "openclaw", "auto_apply": True, "age_gate_status": "ready"},
            {"name": "GitHub CLI", "auto_apply": False, "age_gate_status": "unknown"},
        ]
    )

    assert [item["name"] for item in queue] == ["openclaw"]


def test_load_apply_result_returns_empty_payload_when_missing(tmp_path) -> None:
    result = load_apply_result(tmp_path / "missing.json")

    assert result == {"applied": [], "failed": [], "skipped": []}


def test_run_startup_cycle_does_not_apply_ready_updates_by_default(tmp_path, monkeypatch) -> None:
    base_dir = tmp_path
    (base_dir / "artifacts").mkdir()
    calls: list[tuple[str, tuple[str, ...]]] = []
    updates = [
        {
            "name": "openclaw",
            "current_version": "1.0.0",
            "latest_version": "1.1.0",
            "manager": "npm",
            "channel": "npm-global",
            "auto_apply": True,
            "age_gate_status": "ready",
        }
    ]

    def fake_run_python_script(_: Path, script_name: str, *args: str) -> None:
        calls.append((script_name, args))

    monkeypatch.setattr("startup_runner.run_python_script", fake_run_python_script)
    monkeypatch.setattr("startup_runner.load_updates_only", lambda _: updates)
    monkeypatch.setattr("startup_runner.open_report", lambda _: None)

    run_log = run_startup_cycle(
        base_dir,
        now=datetime(2026, 4, 11, 9, 0, tzinfo=timezone.utc),
    )

    assert run_log is not None
    assert run_log["updates_applied"] is False
    assert ("apply_safe_updates.py", ("--execute",)) not in calls
    assert calls == [
        ("update_audit.py", ()),
        ("render_updates_only.py", ()),
    ]


def test_run_startup_cycle_applies_ready_updates_only_when_explicitly_requested(
    tmp_path, monkeypatch
) -> None:
    base_dir = tmp_path
    (base_dir / "artifacts").mkdir()
    calls: list[tuple[str, tuple[str, ...]]] = []
    updates_before = [
        {
            "name": "openclaw",
            "current_version": "1.0.0",
            "latest_version": "1.1.0",
            "manager": "npm",
            "channel": "npm-global",
            "auto_apply": True,
            "release_published_at": "2026-04-08T08:00:00Z",
            "eligible_after": "2026-04-11T08:00:00Z",
            "age_gate_status": "ready",
            "age_gate_reason": None,
        }
    ]
    updates_after: list[dict] = []
    update_batches = iter([updates_before, updates_after])

    def fake_run_python_script(_: Path, script_name: str, *args: str) -> None:
        calls.append((script_name, args))

    monkeypatch.setattr("startup_runner.run_python_script", fake_run_python_script)
    monkeypatch.setattr(
        "startup_runner.load_updates_only",
        lambda _: next(update_batches),
    )
    monkeypatch.setattr(
        "startup_runner.load_apply_result",
        lambda _: {
            "applied": [
                {
                    "name": "openclaw",
                    "current_version": "1.0.0",
                    "latest_version": "1.1.0",
                }
            ],
            "failed": [],
            "skipped": [],
        },
    )
    monkeypatch.setattr(
        "startup_runner.open_report",
        lambda _: None,
    )

    run_log = run_startup_cycle(
        base_dir,
        now=datetime(2026, 4, 11, 9, 0, tzinfo=timezone.utc),
        apply_updates=True,
    )

    assert run_log is not None
    assert run_log["updates_applied"] is True
    assert [item["name"] for item in run_log["applied_updates"]] == ["openclaw"]
    assert ("apply_safe_updates.py", ("--execute",)) in calls
    assert calls == [
        ("update_audit.py", ()),
        ("render_updates_only.py", ()),
        ("apply_safe_updates.py", ("--execute",)),
        ("update_audit.py", ()),
        ("render_updates_only.py", ()),
    ]


def test_run_startup_cycle_runs_post_update_rehydrate_after_auto_update(
    tmp_path, monkeypatch
) -> None:
    base_dir = tmp_path
    (base_dir / "artifacts").mkdir()
    update_batches = iter(
        [
            [
                {
                    "name": "openclaw",
                    "current_version": "1.0.0",
                    "latest_version": "1.1.0",
                    "manager": "npm",
                    "channel": "npm-global",
                    "auto_apply": True,
                    "release_published_at": "2026-04-08T08:00:00Z",
                    "eligible_after": "2026-04-11T08:00:00Z",
                    "age_gate_status": "ready",
                    "age_gate_reason": None,
                }
            ],
            [],
        ]
    )
    hook_calls: list[str] = []

    monkeypatch.setattr("startup_runner.run_python_script", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("startup_runner.load_updates_only", lambda _: next(update_batches))
    monkeypatch.setattr(
        "startup_runner.load_apply_result",
        lambda _: {
            "applied": [
                {
                    "name": "openclaw",
                    "current_version": "1.0.0",
                    "latest_version": "1.1.0",
                }
            ],
            "failed": [],
            "skipped": [],
        },
    )
    monkeypatch.setattr("startup_runner.open_report", lambda _: None)
    monkeypatch.setattr(
        "startup_runner.run_post_update_rehydrate",
        lambda: hook_calls.append("rehydrate")
        or {
            "status": "already-current",
            "archive_root": r"C:\Users\example\AppData\Local\uv\cache\archive-v0\demo",
        },
    )

    run_log = run_startup_cycle(
        base_dir,
        now=datetime(2026, 4, 11, 9, 0, tzinfo=timezone.utc),
        apply_updates=True,
    )

    assert run_log is not None
    assert hook_calls == ["rehydrate"]
    assert run_log["post_update_rehydrate"]["status"] == "already-current"


def test_resolve_run_status_prefers_applied_updates() -> None:
    assert resolve_run_status({"updates_applied": True, "changed_updates": ["rtk"], "updates_pending_count": 1}) == "обновления применены"


def test_resolve_run_status_handles_pending_without_new_items() -> None:
    assert resolve_run_status({"updates_applied": False, "changed_updates": [], "updates_pending_count": 3}) == "ожидание обновлений"


def test_choose_python_launcher_prefers_pythonw(tmp_path) -> None:
    base = tmp_path / "py"
    base.mkdir()
    pythonw = base / "pythonw.exe"
    python = base / "python.exe"
    pythonw.write_text("", encoding="utf-8")
    python.write_text("", encoding="utf-8")

    chosen = choose_python_launcher(str(python))

    assert chosen == str(pythonw)


def test_launcher_content_targets_startup_runner() -> None:
    content = launcher_content(Path(r"C:\Users\example\ToolUpdateAuditor"), r"C:\Python312\pythonw.exe")

    assert "startup_runner.py" in content
    assert "pythonw.exe" in content


def test_launcher_content_includes_auto_apply_only_when_requested() -> None:
    content = launcher_content(
        Path(r"C:\Users\example\ToolUpdateAuditor"),
        r"C:\Python312\pythonw.exe",
        apply_updates=True,
    )

    assert "--apply-ready" in content


def test_resolve_windows_user_home_prefers_real_profile_over_codex_store_home(tmp_path) -> None:
    fake_home = tmp_path / ".codex-store-userhome"
    fake_home.mkdir(parents=True)
    real_users_root = tmp_path / "Users"
    real_home = real_users_root / "example"
    (real_home / "AppData" / "Roaming").mkdir(parents=True)

    resolved = resolve_windows_user_home(
        home=fake_home,
        userprofile=str(fake_home),
        username="example",
        windows_users_root=real_users_root,
    )

    assert resolved == real_home


def test_lock_is_stale_when_started_at_missing() -> None:
    assert lock_is_stale({"pid": 1234}, now=datetime(2026, 3, 22, tzinfo=timezone.utc)) is True


def test_windows_process_liveness_uses_exit_code_without_os_kill(monkeypatch) -> None:
    class FakeKernel32:
        def __init__(self) -> None:
            self.closed_handles: list[int] = []

        def OpenProcess(self, _access: int, _inherit: bool, _pid: int) -> int:
            return 100

        def GetExitCodeProcess(self, _handle: int, exit_code) -> int:
            exit_code._obj.value = 0
            return 1

        def CloseHandle(self, handle: int) -> int:
            self.closed_handles.append(handle)
            return 1

    kernel32 = FakeKernel32()
    monkeypatch.setattr(startup_runner.sys, "platform", "win32")
    monkeypatch.setattr(startup_runner.ctypes, "windll", type("Windll", (), {"kernel32": kernel32})(), raising=False)
    monkeypatch.setattr(
        startup_runner.os,
        "kill",
        lambda *_args: (_ for _ in ()).throw(AssertionError("Windows path must not use os.kill")),
    )

    assert startup_runner.is_process_running(1234) is False
    assert kernel32.closed_handles == [100]


def test_windows_process_liveness_treats_still_active_as_running(monkeypatch) -> None:
    class FakeKernel32:
        def OpenProcess(self, _access: int, _inherit: bool, _pid: int) -> int:
            return 100

        def GetExitCodeProcess(self, _handle: int, exit_code) -> int:
            exit_code._obj.value = 259
            return 1

        def CloseHandle(self, _handle: int) -> int:
            return 1

    monkeypatch.setattr(startup_runner.sys, "platform", "win32")
    monkeypatch.setattr(
        startup_runner.ctypes,
        "windll",
        type("Windll", (), {"kernel32": FakeKernel32()})(),
        raising=False,
    )

    assert startup_runner.is_process_running(1234) is True


def test_acquire_startup_lock_blocks_second_instance(tmp_path) -> None:
    lock_path = tmp_path / "startup-runner.lock"

    with acquire_startup_lock(lock_path) as first_acquired:
        with acquire_startup_lock(lock_path) as second_acquired:
            assert first_acquired is True
            assert second_acquired is False


def test_acquire_startup_lock_does_not_steal_unreadable_lock(tmp_path) -> None:
    lock_path = tmp_path / "startup-runner.lock"
    lock_path.write_text("{", encoding="utf-8")

    with acquire_startup_lock(lock_path) as acquired:
        assert acquired is False

    assert lock_path.read_text(encoding="utf-8") == "{"

