from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess

from configure_release_age_policy import build_release_age_policy_files
from update_audit import (
    ReleaseMetadata,
    apply_release_age_policy,
    ClassifiedToolRecord,
    ToolRecord,
    _parse_npm_outdated_json,
    _parse_scoop_list,
    _scan_managed_codex_lb,
    _parse_winget_upgrade,
    _parse_winget_list,
    build_safe_update_plan,
    enrich_latest_versions,
    classify_record,
    default_bucket_raw_bases,
    default_policy_rules,
    github_release_info_from_payload,
    latest_from_github_release_payload,
    latest_from_npm_registry_payload,
    npm_publish_time_from_payload,
    collect_pypi_metadata,
    latest_from_pypi_payload,
    pypi_publish_time_from_payload,
    pypi_metadata_enabled,
    enrich_latest_versions,
    latest_from_scoop_manifest_payload,
    _run_command,
    render_updates_only_markdown_report,
    render_markdown_report,
    resolve_executable,
)


def test_classifies_local_bin_rtk_as_github_lastversion() -> None:
    record = ToolRecord(
        name="rtk",
        current_version="0.29.0",
        source="local-bin",
        manager="local-bin",
        install_path="C:/Users/example/.local/bin/rtk.exe",
    )

    classified = classify_record(record, default_policy_rules())

    assert classified.update_channel == "github-lastversion"
    assert classified.recommended_command == r"lastversion --at C:\Users\example\.local\bin rtk"


def test_parses_winget_source_without_misreading_available_version() -> None:
    raw = "PowerShell 7-x64                        Microsoft.PowerShell                     7.6.0.0                        winget"

    parsed = _parse_winget_list(raw)

    assert len(parsed) == 1
    assert parsed[0].name == "PowerShell 7-x64"
    assert parsed[0].current_version == "7.6.0.0"
    assert parsed[0].latest_version is None
    assert parsed[0].source == "winget"


def test_skips_scoop_separator_rows() -> None:
    raw = """Installed apps:

Name             Version            Source   Updated             Info
----             -------            ------   -------             ----
7zip             26.00              main     2026-03-01 11:23:28
"""

    parsed = _parse_scoop_list(raw)

    assert [record.name for record in parsed] == ["7zip"]


def test_render_markdown_report_has_summary() -> None:
    report = render_markdown_report(
        [classify_record(ToolRecord(name="rtk", manager="local-bin", install_path="C:/Users/example/.local/bin/rtk.exe"), default_policy_rules())]
    )

    assert "# Tool Update Audit" in report
    assert "| github-lastversion | 1 |" in report


def test_render_markdown_report_has_release_age_columns() -> None:
    report = render_markdown_report(
        [
            ClassifiedToolRecord(
                name="openclaw",
                manager="npm",
                current_version="2026.3.13",
                latest_version="2026.3.28",
                update_channel="npm-global",
                recommended_command="npm -g update openclaw",
                auto_apply=True,
                release_published_at="2026-04-01T10:15:00Z",
                eligible_after="2026-04-04T10:15:00Z",
                age_gate_status="deferred",
            )
        ]
    )

    assert "| Published | Eligible after | Age status |" in report
    assert "2026-04-01T10:15:00Z" in report
    assert "2026-04-04T10:15:00Z" in report
    assert "deferred" in report


def test_resolve_executable_uses_fallback_shim_dir(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    shim_dir = home / "scoop" / "shims"
    shim_dir.mkdir(parents=True)
    shim_file = shim_dir / "scoop.cmd"
    shim_file.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr("update_audit.shutil.which", lambda _: None)

    resolved = resolve_executable("scoop", home)

    assert resolved == str(shim_file)


def test_run_command_returns_empty_output_on_timeout(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="npm outdated -g --json", timeout=15)

    monkeypatch.setattr("update_audit.subprocess.run", fake_run)
    monkeypatch.setattr("update_audit.resolve_executable", lambda _: "npm")

    assert _run_command(["npm", "outdated", "-g", "--json"], timeout_seconds=15) == ""


def test_parse_winget_upgrade_extracts_available_versions() -> None:
    raw = """Name                               Id                                  Version             Available            Source
-----------------------------------------------------------------------------------------------------------------------
GitHub CLI                         GitHub.cli                          2.85.0              2.88.1               winget
Windows Terminal                   Microsoft.WindowsTerminal           1.23.13503.0        1.24.10621.0         winget
"""

    parsed = _parse_winget_upgrade(raw)

    assert parsed["GitHub.cli"] == "2.88.1"
    assert parsed["Microsoft.WindowsTerminal"] == "1.24.10621.0"


def test_parse_npm_outdated_json_extracts_latest_versions() -> None:
    raw = """{
  "@openai/codex": {
    "current": "0.116.0",
    "wanted": "0.117.0",
    "latest": "0.117.0"
  },
  "pnpm": {
    "current": "10.30.1",
    "wanted": "10.32.1",
    "latest": "10.32.1"
  }
}"""

    parsed = _parse_npm_outdated_json(raw)

    assert parsed["@openai/codex"] == "0.117.0"
    assert parsed["pnpm"] == "10.32.1"


def test_latest_from_pypi_payload() -> None:
    payload = '{"info": {"version": "0.3.0"}}'

    assert latest_from_pypi_payload(payload) == "0.3.0"


def test_latest_from_github_release_payload() -> None:
    payload = '{"tag_name": "v0.31.0", "name": "v0.31.0"}'

    assert latest_from_github_release_payload(payload) == "0.31.0"


def test_github_release_payload_returns_version_and_publish_time() -> None:
    payload = '{"tag_name": "v0.31.0", "published_at": "2026-03-29T10:15:00Z"}'

    assert github_release_info_from_payload(payload) == ("0.31.0", "2026-03-29T10:15:00Z")


def test_npm_registry_payload_returns_publish_time_for_exact_version() -> None:
    payload = """{
      "time": {
        "1.0.0": "2026-03-27T09:00:00.000Z",
        "1.1.0": "2026-03-31T11:00:00.000Z"
      }
    }"""

    assert npm_publish_time_from_payload(payload, "1.1.0") == "2026-03-31T11:00:00.000Z"


def test_npm_registry_payload_returns_none_when_exact_version_missing() -> None:
    payload = """{
      "time": {
        "1.0.0": "2026-03-27T09:00:00.000Z"
      }
    }"""

    assert npm_publish_time_from_payload(payload, "1.1.0") is None


def test_latest_from_npm_registry_payload_prefers_dist_tag_latest() -> None:
    payload = """{
      "dist-tags": {
        "latest": "0.121.0"
      }
    }"""

    assert latest_from_npm_registry_payload(payload) == "0.121.0"


def test_pypi_metadata_enabled_defaults_to_true() -> None:
    assert pypi_metadata_enabled({}) is True


def test_pypi_metadata_enabled_can_be_disabled_explicitly() -> None:
    assert pypi_metadata_enabled({"TOOL_AUDIT_DISABLE_PYPI": "true"}) is False


def test_pypi_payload_returns_upload_time_for_exact_version() -> None:
    payload = """{
      "releases": {
        "0.3.0": [
          {"upload_time_iso_8601": "2026-03-28T08:30:00.000Z"}
        ]
      }
    }"""

    assert pypi_publish_time_from_payload(payload, "0.3.0") == "2026-03-28T08:30:00.000Z"


def test_pypi_payload_returns_none_when_release_files_missing() -> None:
    payload = """{
      "releases": {
        "0.3.0": []
      }
    }"""

    assert pypi_publish_time_from_payload(payload, "0.3.0") is None


def test_enrich_latest_versions_uses_manager_specific_sources() -> None:
    records = [
        ToolRecord(name="GitHub CLI", manager="winget", package_id="GitHub.cli", current_version="2.85.0"),
        ToolRecord(name="@openai/codex", manager="npm", current_version="0.116.0"),
        ToolRecord(name="nano-pdf", manager="uv-tool", current_version="0.2.1"),
        ToolRecord(name="rtk", manager="local-bin", install_path="C:/Users/example/.local/bin/rtk.exe"),
    ]

    enriched = enrich_latest_versions(
        records,
        winget_latest={"GitHub.cli": "2.88.1"},
        npm_latest={"@openai/codex": "0.117.0"},
        pypi_latest={"nano-pdf": "0.3.0"},
        github_latest={"rtk": "0.31.0"},
    )

    assert enriched[0].latest_version == "2.88.1"
    assert enriched[1].latest_version == "0.117.0"
    assert enriched[2].latest_version == "0.3.0"
    assert enriched[3].latest_version == "0.31.0"


def test_enrich_latest_versions_carries_publish_metadata() -> None:
    records = [
        ToolRecord(name="@openai/codex", manager="npm", current_version="0.116.0"),
        ToolRecord(name="nano-pdf", manager="uv-tool", current_version="0.2.1"),
        ToolRecord(name="rtk", manager="local-bin", install_path="C:/Users/example/.local/bin/rtk.exe"),
    ]

    enriched = enrich_latest_versions(
        records,
        npm_latest={"@openai/codex": "0.117.0"},
        pypi_latest={"nano-pdf": "0.3.0"},
        github_latest={"rtk": "0.31.0"},
        npm_metadata={"@openai/codex": ReleaseMetadata(version="0.117.0", published_at="2026-03-31T11:00:00Z", source="npm-registry")},
        pypi_metadata={"nano-pdf": ReleaseMetadata(version="0.3.0", published_at="2026-03-28T08:30:00Z", source="pypi-json")},
        github_metadata={"rtk": ReleaseMetadata(version="0.31.0", published_at="2026-03-29T10:15:00Z", source="github-release")},
    )

    assert enriched[0].latest_version == "0.117.0"
    assert enriched[0].release_published_at == "2026-03-31T11:00:00Z"
    assert enriched[0].release_date_source == "npm-registry"
    assert enriched[1].latest_version == "0.3.0"
    assert enriched[1].release_published_at == "2026-03-28T08:30:00Z"
    assert enriched[1].release_date_source == "pypi-json"
    assert enriched[2].latest_version == "0.31.0"
    assert enriched[2].release_published_at == "2026-03-29T10:15:00Z"
    assert enriched[2].release_date_source == "github-release"


def test_enrich_latest_versions_prefers_npm_registry_metadata_version_over_constrained_outdated_result() -> None:
    records = [
        ToolRecord(name="@openai/codex", manager="npm", current_version="0.120.0"),
    ]

    enriched = enrich_latest_versions(
        records,
        npm_latest={"@openai/codex": "0.118.0"},
        npm_metadata={"@openai/codex": ReleaseMetadata(version="0.121.0", published_at="2026-04-11T10:00:00Z", source="npm-registry")},
    )

    assert enriched[0].latest_version == "0.121.0"
    assert enriched[0].release_published_at == "2026-04-11T10:00:00Z"


def test_classifies_openclaw_doctor_as_npm_global_shim() -> None:
    record = ToolRecord(
        name="openclaw-doctor",
        manager="local-bin",
        install_path="C:/Users/example/.local/bin/openclaw-doctor.cmd",
    )

    classified = classify_record(record, default_policy_rules())

    assert classified.update_channel == "npm-global-shim"
    assert classified.recommended_command == "npm -g update openclaw"


def test_classifies_openclaw_as_safe_npm_global_update() -> None:
    record = ToolRecord(
        name="openclaw",
        manager="npm",
        current_version="2026.3.13",
        latest_version="2026.3.28",
    )

    classified = classify_record(record, default_policy_rules())

    assert classified.update_channel == "npm-global"
    assert classified.auto_apply is True
    assert classified.recommended_command == "npm -g update openclaw"


def test_classifies_openai_coding_runtimes_as_manual_only_to_avoid_live_binary_locking() -> None:
    codex = classify_record(
        ToolRecord(name="@openai/codex", manager="npm", current_version="0.118.0", latest_version="0.120.0"),
        default_policy_rules(),
    )
    claude_code = classify_record(
        ToolRecord(name="@anthropic-ai/claude-code", manager="npm", current_version="2.1.96", latest_version="2.1.104"),
        default_policy_rules(),
    )

    assert codex.update_channel == "manual-only"
    assert codex.auto_apply is False
    assert codex.release_age_bypass is False
    assert codex.recommended_command == "npm -g update @openai/codex"
    assert "live binaries" in (codex.skip_reason or "")
    assert claude_code.update_channel == "manual-only"
    assert claude_code.auto_apply is False
    assert claude_code.release_age_bypass is False
    assert claude_code.recommended_command == "npm -g update @anthropic-ai/claude-code"
    assert "live binaries" in (claude_code.skip_reason or "")


def test_scans_managed_codex_lb_from_wrapper_pin(tmp_path) -> None:
    home = tmp_path / "home"
    wrapper_path = home / ".codex" / "codex-lb-wrapper.ps1"
    executable_path = home / ".codex" / "vendor" / "codex-lb" / "venv-1.14.0" / "Scripts" / "codex-lb.exe"
    site_packages = executable_path.parent.parent / "Lib" / "site-packages"
    dist_info = site_packages / "codex_lb-1.14.0.dist-info"
    dist_info.mkdir(parents=True)
    executable_path.parent.mkdir(parents=True, exist_ok=True)
    executable_path.write_text("", encoding="utf-8")
    (dist_info / "METADATA").write_text("Name: codex-lb\nVersion: 1.14.0\n", encoding="utf-8")
    wrapper_path.parent.mkdir(parents=True, exist_ok=True)
    wrapper_path.write_text(f'$pinnedExe = "{executable_path}"\n', encoding="utf-8")

    record = _scan_managed_codex_lb(home)

    assert record is not None
    assert record.name == "codex-lb"
    assert record.manager == "managed-runtime"
    assert record.package_id == "codex-lb"
    assert record.current_version == "1.14.0"
    assert record.install_path == str(executable_path)


def test_collect_pypi_metadata_supports_managed_runtime(monkeypatch) -> None:
    payload = '{"info":{"version":"1.15.0"},"releases":{"1.15.0":[{"upload_time_iso_8601":"2026-04-22T00:00:00Z"}]}}'
    monkeypatch.setattr("update_audit._fetch_text", lambda _: payload)

    metadata = collect_pypi_metadata([ToolRecord(name="codex-lb", manager="managed-runtime")])

    assert metadata["codex-lb"].version == "1.15.0"
    assert metadata["codex-lb"].published_at == "2026-04-22T00:00:00Z"
    assert metadata["codex-lb"].source == "pypi-json"


def test_enrich_latest_versions_supports_managed_runtime_pypi_metadata() -> None:
    enriched = enrich_latest_versions(
        [ToolRecord(name="codex-lb", manager="managed-runtime", current_version="1.14.0")],
        pypi_latest={"codex-lb": "1.15.0"},
        pypi_metadata={"codex-lb": ReleaseMetadata(version="1.15.0", published_at="2026-04-22T00:00:00Z", source="pypi-json")},
    )

    assert enriched[0].latest_version == "1.15.0"
    assert enriched[0].release_published_at == "2026-04-22T00:00:00Z"
    assert enriched[0].release_date_source == "pypi-json"


def test_classifies_managed_codex_lb_as_manual_only_staged_runtime() -> None:
    classified = classify_record(
        ToolRecord(name="codex-lb", manager="managed-runtime", current_version="1.14.0", latest_version="1.15.0"),
        default_policy_rules(),
    )

    assert classified.update_channel == "manual-only"
    assert classified.auto_apply is False
    assert classified.release_age_bypass is False
    assert classified.recommended_command.endswith(r'.codex\scripts\update_codex_lb.py" --activate')
    assert "staged side-by-side upgrades" in (classified.skip_reason or "")
    assert build_safe_update_plan([classified]) == []


def test_classifies_winget_default_as_auto_apply() -> None:
    record = ToolRecord(
        name="GitHub CLI",
        manager="winget",
        package_id="GitHub.cli",
        current_version="2.85.0",
        latest_version="2.89.0",
    )

    classified = classify_record(record, default_policy_rules())

    assert classified.update_channel == "winget"
    assert classified.auto_apply is True
    assert classified.recommended_command == "winget upgrade --id GitHub.cli --source winget"


def test_classifies_winget_unknown_current_version_as_manual_only_with_include_unknown_flag() -> None:
    record = ToolRecord(
        name="DeepL",
        manager="winget",
        package_id="DeepL.DeepL",
        current_version="Unknown",
        latest_version="26.4.1",
    )

    classified = classify_record(record, default_policy_rules())

    assert classified.update_channel == "manual-only"
    assert classified.auto_apply is False
    assert classified.recommended_command == "winget upgrade --id DeepL.DeepL --source winget --include-unknown"
    assert "installed version" in (classified.skip_reason or "")
    assert build_safe_update_plan([classified]) == []


def test_classifies_opencode_desktop_as_auto_apply_winget_update() -> None:
    record = ToolRecord(
        name="OpenCode",
        manager="winget",
        package_id="SST.OpenCodeDesktop",
        current_version="1.4.0",
        latest_version="1.4.3",
    )

    classified = classify_record(record, default_policy_rules())

    assert classified.update_channel == "winget"
    assert classified.auto_apply is True
    assert classified.release_age_bypass is True
    assert classified.recommended_command == "winget upgrade --id SST.OpenCodeDesktop --source winget"


def test_classifies_allowlisted_antigravity_extensions_as_auto_apply_host_updates() -> None:
    opencode_extension = classify_record(
        ToolRecord(name="sst-dev.opencode", manager="antigravity-extension", current_version="0.0.13-universal"),
        default_policy_rules(),
    )
    iflow_extension = classify_record(
        ToolRecord(name="yaumike.iflow-for-vscode", manager="antigravity-extension", current_version="0.1.9-universal"),
        default_policy_rules(),
    )

    assert opencode_extension.update_channel == "antigravity-extension-safe"
    assert opencode_extension.auto_apply is True
    assert opencode_extension.release_age_bypass is True
    assert opencode_extension.recommended_command == "antigravity --install-extension sst-dev.opencode --force"
    assert iflow_extension.update_channel == "antigravity-extension-safe"
    assert iflow_extension.auto_apply is True
    assert iflow_extension.release_age_bypass is True
    assert iflow_extension.recommended_command == "antigravity --install-extension yaumike.iflow-for-vscode --force"


def test_classifies_antigravity_as_manual_only() -> None:
    record = ToolRecord(
        name="Antigravity (User)",
        manager="winget",
        package_id="Google.Antigravity",
        current_version="1.20.6",
        latest_version="1.20.7",
    )

    classified = classify_record(record, default_policy_rules())

    assert classified.update_channel == "manual-only"
    assert "silent startup regressions" in (classified.skip_reason or "")


def test_classifies_python_shims_as_manual_only() -> None:
    record = ToolRecord(
        name="python3.13",
        manager="local-bin",
        install_path="C:/Users/example/.local/bin/python3.13.exe",
    )

    classified = classify_record(record, default_policy_rules())

    assert classified.update_channel == "manual-only"
    assert "scoop update python" in classified.recommended_command


def test_updates_only_report_filters_to_real_updates() -> None:
    records = [
        classify_record(
            ToolRecord(name="rtk", manager="local-bin", current_version="0.29.0", latest_version="0.31.0", install_path="C:/Users/example/.local/bin/rtk.exe"),
            default_policy_rules(),
        ),
        classify_record(
            ToolRecord(name="PowerShell 7-x64", manager="winget", package_id="Microsoft.PowerShell", current_version="7.6.0.0", latest_version=None),
            default_policy_rules(),
        ),
        classify_record(
            ToolRecord(name="pnpm", manager="npm", current_version="10.30.1", latest_version="10.32.1"),
            default_policy_rules(),
        ),
    ]

    report = render_updates_only_markdown_report(records)

    assert "# Tool Updates Only" in report
    assert "rtk" in report
    assert "pnpm" in report
    assert "PowerShell 7-x64" not in report


def test_updates_only_report_has_release_age_columns() -> None:
    report = render_updates_only_markdown_report(
        [
            ClassifiedToolRecord(
                name="openclaw",
                manager="npm",
                current_version="2026.3.13",
                latest_version="2026.3.28",
                update_channel="npm-global",
                recommended_command="npm -g update openclaw",
                auto_apply=True,
                release_published_at="2026-04-01T10:15:00Z",
                eligible_after="2026-04-04T10:15:00Z",
                age_gate_status="deferred",
            )
        ]
    )

    assert "| Published | Eligible after | Age status |" in report
    assert "2026-04-01T10:15:00Z" in report
    assert "2026-04-04T10:15:00Z" in report
    assert "deferred" in report


def test_latest_from_scoop_manifest_payload() -> None:
    payload = '{"version": "2.88.1"}'

    assert latest_from_scoop_manifest_payload(payload) == "2.88.1"


def test_default_bucket_raw_bases_from_standard_remotes() -> None:
    bases = default_bucket_raw_bases(
        {
            "main": "https://github.com/ScoopInstaller/Main.git",
            "extras": "https://github.com/ScoopInstaller/Extras",
            "versions": "https://github.com/ScoopInstaller/Versions",
        }
    )

    assert bases["main"][0] == "https://raw.githubusercontent.com/ScoopInstaller/Main/master/bucket"
    assert bases["extras"][1] == "https://raw.githubusercontent.com/ScoopInstaller/Extras/main/bucket"


def test_build_safe_update_plan_only_keeps_allowed_channels_with_real_updates() -> None:
    records = [
        ClassifiedToolRecord(
            name="rtk",
            manager="local-bin",
            current_version="0.29.0",
            latest_version="0.31.0",
            install_path="C:/Users/example/.local/bin/rtk.exe",
            update_channel="github-lastversion",
            recommended_command="lastversion --at C:/Users/example/.local/bin rtk",
            auto_apply=True,
            age_gate_status="ready",
        ),
        classify_record(
            ToolRecord(name="PowerShell 7-x64", manager="winget", package_id="Microsoft.PowerShell", current_version="7.6.0.0", latest_version="7.6.1.0"),
            default_policy_rules(),
        ),
        ClassifiedToolRecord(
            name="pnpm",
            manager="npm",
            current_version="10.30.1",
            latest_version="10.32.1",
            update_channel="npm-global",
            recommended_command="npm -g update pnpm",
            auto_apply=True,
            age_gate_status="ready",
        ),
        classify_record(
            ToolRecord(name="openai.chatgpt", manager="antigravity-extension", current_version="26.318.11754", latest_version="26.400.0"),
            default_policy_rules(),
        ),
    ]

    plan = build_safe_update_plan(records)

    assert [item.name for item in plan] == ["pnpm", "rtk"]


def test_build_safe_update_plan_excludes_interactive_ai_runtimes_even_when_updates_exist() -> None:
    records = [
        classify_record(
            ToolRecord(name="@openai/codex", manager="npm", current_version="0.121.0", latest_version="0.122.0"),
            default_policy_rules(),
        ),
        classify_record(
            ToolRecord(name="@anthropic-ai/claude-code", manager="npm", current_version="2.1.96", latest_version="2.1.104"),
            default_policy_rules(),
        ),
        ClassifiedToolRecord(
            name="pnpm",
            manager="npm",
            current_version="10.30.1",
            latest_version="10.32.1",
            update_channel="npm-global",
            recommended_command="npm -g update pnpm",
            auto_apply=True,
            age_gate_status="ready",
        ),
    ]

    plan = build_safe_update_plan(records)

    assert [item.name for item in plan] == ["pnpm"]


def test_build_safe_update_plan_excludes_deferred_release() -> None:
    records = apply_release_age_policy(
        [
            ClassifiedToolRecord(
                name="openclaw",
                manager="npm",
                current_version="2026.3.13",
                latest_version="2026.3.28",
                update_channel="npm-global",
                recommended_command="npm -g update openclaw",
                auto_apply=True,
                release_published_at="2026-04-01T10:15:00Z",
            )
        ],
        now_utc=datetime(2026, 4, 2, 9, 0, tzinfo=timezone.utc),
    )

    plan = build_safe_update_plan(records)

    assert records[0].age_gate_status == "deferred"
    assert records[0].eligible_after == "2026-04-04T10:15:00Z"
    assert plan == []


def test_build_safe_update_plan_keeps_ready_release() -> None:
    records = apply_release_age_policy(
        [
            ClassifiedToolRecord(
                name="openclaw",
                manager="npm",
                current_version="2026.3.13",
                latest_version="2026.3.28",
                update_channel="npm-global",
                recommended_command="npm -g update openclaw",
                auto_apply=True,
                release_published_at="2026-03-28T10:15:00Z",
            )
        ],
        now_utc=datetime(2026, 4, 2, 10, 16, tzinfo=timezone.utc),
    )

    plan = build_safe_update_plan(records)

    assert records[0].age_gate_status == "ready"
    assert records[0].eligible_after == "2026-03-31T10:15:00Z"
    assert [item.name for item in plan] == ["openclaw"]


def test_apply_release_age_policy_bypasses_wait_for_allowlisted_runtime() -> None:
    records = apply_release_age_policy(
        [
            ClassifiedToolRecord(
                name="@openai/codex",
                manager="npm",
                current_version="0.118.0",
                latest_version="0.120.0",
                update_channel="npm-global",
                recommended_command="npm -g update @openai/codex",
                auto_apply=True,
                release_published_at="2026-04-12T02:54:37Z",
                release_age_bypass=True,
            )
        ],
        now_utc=datetime(2026, 4, 12, 3, 0, tzinfo=timezone.utc),
    )

    plan = build_safe_update_plan(records)

    assert records[0].age_gate_status == "ready"
    assert [item.name for item in plan] == ["@openai/codex"]


def test_apply_release_age_policy_uses_first_seen_fallback_for_auto_apply_without_publish_time() -> None:
    first_seen_by_update: dict[str, str] = {}
    records = apply_release_age_policy(
        [
            ClassifiedToolRecord(
                name="GitHub CLI",
                manager="winget",
                package_id="GitHub.cli",
                current_version="2.85.0",
                latest_version="2.89.0",
                update_channel="winget",
                recommended_command="winget upgrade --id GitHub.cli --source winget",
                auto_apply=True,
            )
        ],
        now_utc=datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc),
        first_seen_by_update=first_seen_by_update,
    )

    assert records[0].age_gate_status == "deferred"
    assert records[0].age_gate_reason == "waiting 72 hours from first detection"
    assert records[0].eligible_after == "2026-04-15T10:00:00Z"
    assert first_seen_by_update == {
        '["winget","GitHub.cli","2.85.0","2.89.0"]': "2026-04-12T10:00:00Z"
    }


def test_apply_release_age_policy_marks_first_seen_fallback_ready_after_72h() -> None:
    first_seen_by_update = {
        '["winget","GitHub.cli","2.85.0","2.89.0"]': "2026-04-12T10:00:00Z"
    }
    records = apply_release_age_policy(
        [
            ClassifiedToolRecord(
                name="GitHub CLI",
                manager="winget",
                package_id="GitHub.cli",
                current_version="2.85.0",
                latest_version="2.89.0",
                update_channel="winget",
                recommended_command="winget upgrade --id GitHub.cli --source winget",
                auto_apply=True,
            )
        ],
        now_utc=datetime(2026, 4, 15, 10, 1, tzinfo=timezone.utc),
        first_seen_by_update=first_seen_by_update,
    )

    plan = build_safe_update_plan(records)

    assert records[0].age_gate_status == "ready"
    assert records[0].eligible_after == "2026-04-15T10:00:00Z"
    assert [item.name for item in plan] == ["GitHub CLI"]


def test_apply_release_age_policy_scopes_first_seen_fallback_to_exact_update_tuple() -> None:
    first_seen_by_update = {
        '["winget","GitHub.cli","2.85.0","2.89.0"]': "2026-04-12T10:00:00Z"
    }
    records = apply_release_age_policy(
        [
            ClassifiedToolRecord(
                name="GitHub CLI",
                manager="winget",
                package_id="GitHub.cli",
                current_version="2.85.0",
                latest_version="2.90.0",
                update_channel="winget",
                recommended_command="winget upgrade --id GitHub.cli --source winget",
                auto_apply=True,
            )
        ],
        now_utc=datetime(2026, 4, 12, 12, 0, tzinfo=timezone.utc),
        first_seen_by_update=first_seen_by_update,
    )

    assert records[0].age_gate_status == "deferred"
    assert records[0].eligible_after == "2026-04-15T12:00:00Z"
    assert first_seen_by_update == {
        '["winget","GitHub.cli","2.85.0","2.90.0"]': "2026-04-12T12:00:00Z"
    }


def test_build_safe_update_plan_defers_auto_apply_update_from_first_detection_when_publish_time_is_missing() -> None:
    records = apply_release_age_policy(
        [
            ClassifiedToolRecord(
                name="Windows Terminal",
                manager="winget",
                current_version="1.23.0",
                latest_version="1.24.0",
                package_id="Microsoft.WindowsTerminal",
                update_channel="winget",
                recommended_command="winget upgrade --id Microsoft.WindowsTerminal --source winget",
                auto_apply=True,
            )
        ],
        now_utc=datetime(2026, 4, 2, 10, 16, tzinfo=timezone.utc),
    )

    plan = build_safe_update_plan(records)

    assert records[0].age_gate_status == "deferred"
    assert records[0].age_gate_reason == "waiting 72 hours from first detection"
    assert plan == []


def test_release_age_policy_helper_builds_expected_file_updates(tmp_path) -> None:
    home = tmp_path / "home"
    appdata = tmp_path / "appdata"
    localappdata = tmp_path / "localappdata"
    npmrc = home / ".npmrc"
    uv_toml = appdata / "uv" / "uv.toml"
    pnpm_rc = localappdata / "pnpm" / "config" / "rc"

    updates = build_release_age_policy_files(
        home=home,
        appdata=appdata,
        localappdata=localappdata,
        existing_files={
            npmrc: "fund=false\n",
            uv_toml: 'exclude-newer = "24 hours"\n',
            pnpm_rc: "auto-install-peers=true\n",
        },
    )

    assert updates[npmrc].endswith("min-release-age=3\n")
    assert 'exclude-newer = "72 hours"\n' in updates[uv_toml]
    assert "minimumReleaseAge=4320\n" in updates[pnpm_rc]

