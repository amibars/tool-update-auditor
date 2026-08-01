from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys

STATE_FILE_NAME = "startup-runner-state.json"
RUN_LOG_FILE_NAME = "startup-runner-last-run.json"
APPLY_RESULT_FILE_NAME = "apply-safe-updates-last-run.json"
LOCK_FILE_NAME = "startup-runner.lock"
LOCK_STALE_AFTER = timedelta(hours=2)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def should_run_weekly_updates(last_run_at: datetime | None, *, now: datetime | None = None) -> bool:
    now = now or utc_now()
    if last_run_at is None:
        return True
    return now - last_run_at >= timedelta(days=7)


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def run_python_script(base_dir: Path, script_name: str, *args: str) -> None:
    subprocess.run([sys.executable, str(base_dir / script_name), *args], check=False)


def _is_windows_process_running(pid: int) -> bool:
    process_query_limited_information = 0x1000
    error_access_denied = 5
    still_active = 259

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _is_windows_process_running(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def lock_is_stale(lock_payload: dict, *, now: datetime | None = None) -> bool:
    now = now or utc_now()
    started_at = parse_timestamp(lock_payload.get("started_at"))
    pid = int(lock_payload.get("pid", 0) or 0)
    if started_at is None:
        return True
    if now - started_at >= LOCK_STALE_AFTER:
        return True
    return not is_process_running(pid)


@contextmanager
def acquire_startup_lock(lock_path: Path):
    now = utc_now()
    payload = {
        "pid": os.getpid(),
        "started_at": now.isoformat(),
    }
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            existing_payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            yield False
            return
        if not lock_is_stale(existing_payload, now=now):
            yield False
            return
        try:
            lock_path.unlink()
        except OSError:
            yield False
            return
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True)
        yield True
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass


def load_updates_only(json_path: Path) -> list[dict]:
    if not json_path.exists():
        return []
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    updates = []
    for item in payload:
        current = item.get("current_version")
        latest = item.get("latest_version")
        if current and latest and current != latest:
            updates.append(
                {
                    "name": item.get("name"),
                    "current_version": current,
                    "latest_version": latest,
                    "manager": item.get("manager"),
                    "channel": item.get("update_channel"),
                    "auto_apply": bool(item.get("auto_apply")),
                    "release_published_at": item.get("release_published_at"),
                    "eligible_after": item.get("eligible_after"),
                    "age_gate_status": item.get("age_gate_status"),
                    "age_gate_reason": item.get("age_gate_reason"),
                }
            )
    return updates


def load_apply_result(path: Path) -> dict:
    if not path.exists():
        return {"applied": [], "failed": [], "skipped": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "applied": payload.get("applied", []),
        "failed": payload.get("failed", []),
        "skipped": payload.get("skipped", []),
    }


def build_auto_update_queue(updates: list[dict]) -> list[dict]:
    queue = [item for item in updates if item.get("auto_apply")]
    return sorted(
        queue,
        key=lambda item: (
            item.get("age_gate_status") != "ready",
            item.get("eligible_after") or "",
            item.get("name") or "",
        ),
    )


def has_ready_auto_updates(updates: list[dict]) -> bool:
    return any(item.get("auto_apply") and item.get("age_gate_status") == "ready" for item in updates)


def collect_changed_updates(previous_state: dict, current_updates: list[dict]) -> list[str]:
    previous_updates = previous_state.get("updates_pending", [])
    previous_keys = {
        (
            item.get("name"),
            item.get("current_version"),
            item.get("latest_version"),
        )
        for item in previous_updates
    }
    changed: list[str] = []
    for item in current_updates:
        key = (item.get("name"), item.get("current_version"), item.get("latest_version"))
        if key not in previous_keys and item.get("name"):
            changed.append(str(item["name"]))
    return changed


def should_open_report(
    *,
    changed_updates: list[str],
    updates_applied: bool,
    failed_updates: list[dict] | list[str] | None = None,
) -> bool:
    return bool(changed_updates) or updates_applied or bool(failed_updates)


def open_report(path: Path) -> None:
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except OSError:
        pass


def run_post_update_rehydrate() -> dict:
    return {"status": "not-configured"}


def resolve_run_status(payload: dict) -> str:
    if payload.get("updates_applied"):
        return "обновления применены"
    if payload.get("changed_updates"):
        return "найдены новые обновления"
    if payload.get("updates_pending_count", 0):
        return "ожидание обновлений"
    return "обновлений не найдено"


def _format_auto_in(item: dict, *, now: datetime) -> str:
    status = item.get("age_gate_status")
    if status == "ready":
        return "ready now"
    if status == "unknown":
        return "unknown"
    eligible_after = parse_timestamp(item.get("eligible_after"))
    if eligible_after is None:
        return "-"
    if eligible_after <= now:
        return "ready now"
    remaining = eligible_after - now
    total_hours = int(remaining.total_seconds() // 3600)
    days, hours = divmod(total_hours, 24)
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h"
    return "under 1h"


def _append_table(lines: list[str], headers: list[str], rows: list[list[str]]) -> None:
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")


def render_last_run_report(payload: dict, *, now: datetime | None = None) -> str:
    now = now or utc_now()
    changed_updates = payload.get("changed_updates", [])
    applied_updates = payload.get("applied_updates", [])
    failed_updates = payload.get("failed_updates", [])
    auto_update_queue = payload.get("auto_update_queue", [])
    status = resolve_run_status(payload)
    lines = [
        "# Последний автозапуск ToolUpdateAuditor",
        "",
        f"- статус: {status}",
        f"- время запуска: {payload.get('ran_at', '-')}",
        f"- обновления применялись: {'да' if payload.get('updates_applied') else 'нет'}",
        f"- автообновлено: {len(applied_updates)}",
        f"- ошибок автообновления: {len(failed_updates)}",
        f"- ожидают обновления: {payload.get('updates_pending_count', 0)}",
        f"- в очереди автообновлений: {len(auto_update_queue)}",
        f"- путь к отчету: {payload.get('report_path', '-')}",
        "",
        "## Применено в этот запуск",
    ]
    if applied_updates:
        _append_table(
            lines,
            ["Name", "Current", "Latest"],
            [
                [
                    str(item.get("name", "-")),
                    str(item.get("current_version", "-")),
                    str(item.get("latest_version", "-")),
                ]
                for item in applied_updates
            ],
        )
    else:
        lines.append("- нет")
    lines.extend(
        [
            "",
            "## Новые обновления",
        ]
    )
    if changed_updates:
        for item in changed_updates:
            lines.append(f"- {item}")
    else:
        lines.append("- нет")
    lines.extend(
        [
            "",
            "## Очередь автообновлений",
        ]
    )
    if auto_update_queue:
        _append_table(
            lines,
            ["Name", "Current", "Latest", "Age status", "Eligible after", "Auto in"],
            [
                [
                    str(item.get("name", "-")),
                    str(item.get("current_version", "-")),
                    str(item.get("latest_version", "-")),
                    str(item.get("age_gate_status", "-")),
                    str(item.get("eligible_after", "-")),
                    _format_auto_in(item, now=now),
                ]
                for item in auto_update_queue
            ],
        )
    else:
        lines.append("- нет")
    return "\n".join(lines) + "\n"


def run_startup_cycle(
    base_dir: Path,
    *,
    now: datetime | None = None,
    apply_updates: bool = False,
) -> dict | None:
    artifacts_dir = base_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    lock_path = artifacts_dir / LOCK_FILE_NAME
    state_path = artifacts_dir / STATE_FILE_NAME
    run_log_path = artifacts_dir / RUN_LOG_FILE_NAME
    apply_result_path = artifacts_dir / APPLY_RESULT_FILE_NAME
    audit_json_path = artifacts_dir / "tool-update-audit.json"
    updates_only_path = artifacts_dir / "tool-updates-only.md"
    report_path = artifacts_dir / "startup-runner-last-run.md"
    with acquire_startup_lock(lock_path) as acquired:
        if not acquired:
            return 0

        state = load_state(state_path)

        run_python_script(base_dir, "update_audit.py")
        run_python_script(base_dir, "render_updates_only.py")
        current_updates = load_updates_only(audit_json_path)
        changed_updates = collect_changed_updates(state, current_updates)

        now = now or utc_now()
        updates_applied = False
        apply_result = {"applied": [], "failed": [], "skipped": []}
        post_update_rehydrate = None
        should_apply_now = apply_updates and has_ready_auto_updates(current_updates)
        if should_apply_now:
            run_python_script(base_dir, "apply_safe_updates.py", "--execute")
            apply_result = load_apply_result(apply_result_path)
            state["last_apply_at"] = now.isoformat()
            updates_applied = bool(apply_result["applied"])
            if updates_applied:
                post_update_rehydrate = run_post_update_rehydrate()
            if should_apply_now or apply_result["applied"] or apply_result["failed"]:
                run_python_script(base_dir, "update_audit.py")
                run_python_script(base_dir, "render_updates_only.py")
                current_updates = load_updates_only(audit_json_path)

        state["last_audit_at"] = now.isoformat()
        state["updates_pending"] = current_updates
        save_state(state_path, state)

        auto_update_queue = build_auto_update_queue(current_updates)
        run_log = {
            "ran_at": now.isoformat(),
            "changed_updates": changed_updates,
            "updates_applied": updates_applied,
            "updates_pending_count": len(current_updates),
            "updates_pending": current_updates,
            "applied_updates": apply_result["applied"],
            "failed_updates": apply_result["failed"],
            "skipped_updates": apply_result["skipped"],
            "auto_update_queue": auto_update_queue,
            "report_path": str(report_path),
            "updates_only_path": str(updates_only_path),
        }
        if post_update_rehydrate is not None:
            run_log["post_update_rehydrate"] = post_update_rehydrate
        save_state(run_log_path, run_log)
        report_path.write_text(
            render_last_run_report(run_log),
            encoding="utf-8",
        )

        if should_open_report(
            changed_updates=changed_updates,
            updates_applied=updates_applied,
            failed_updates=apply_result["failed"],
        ):
            open_report(report_path)
        return run_log
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a ToolUpdateAuditor inventory cycle.")
    parser.add_argument(
        "--apply-ready",
        action="store_true",
        help="Explicitly apply policy-approved updates that have passed the release-age gate.",
    )
    args = parser.parse_args()
    base_dir = Path(__file__).resolve().parent
    run_startup_cycle(base_dir, apply_updates=args.apply_ready)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
