from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import importlib.metadata as importlib_metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


KNOWN_WINGET_SOURCES = {"winget", "msstore"}
SAFE_UPDATE_CHANNELS = {
    "antigravity-extension-safe",
    "github-lastversion",
    "npm-global",
    "npm-global-shim",
    "scoop",
    "uv-tool",
    "uv-tool-shim",
    "winget",
}
RELEASE_AGE_FIRST_SEEN_FILE_NAME = "release-age-first-seen.json"
MANAGED_CODEX_LB_NAME = "codex-lb"
MANAGED_CODEX_LB_PACKAGE_ID = "codex-lb"
MANAGED_CODEX_LB_WRAPPER_RELATIVE = Path(".codex") / "codex-lb-wrapper.ps1"
MANAGED_CODEX_LB_PIN_RE = re.compile(r'^\$pinnedExe = "(?P<path>[^"]+)"$', flags=re.MULTILINE)


@dataclass(frozen=True)
class ToolRecord:
    name: str
    current_version: str | None = None
    latest_version: str | None = None
    source: str | None = None
    manager: str | None = None
    package_id: str | None = None
    install_path: str | None = None
    notes: str | None = None
    release_published_at: str | None = None
    release_date_source: str | None = None


@dataclass(frozen=True)
class ReleaseMetadata:
    version: str
    published_at: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class ClassifiedToolRecord(ToolRecord):
    update_channel: str = "unknown"
    recommended_command: str = ""
    risk: str = "medium"
    auto_apply: bool = False
    release_age_bypass: bool = False
    skip_reason: str | None = None
    eligible_after: str | None = None
    age_gate_status: str = "not-applicable"
    age_gate_reason: str | None = None


@dataclass(frozen=True)
class PolicyRule:
    name: str
    match_manager: str | None = None
    match_names: tuple[str, ...] = ()
    match_package_ids: tuple[str, ...] = ()
    update_channel: str = "unknown"
    risk: str = "medium"
    auto_apply: bool = False
    release_age_bypass: bool = False
    command_template: str = ""
    skip_reason: str | None = None

    def matches(self, record: ToolRecord) -> bool:
        if self.match_manager and record.manager != self.match_manager:
            return False

        if self.match_names and record.name.lower() not in {name.lower() for name in self.match_names}:
            return False

        lowered_package_id = (record.package_id or "").lower()
        if self.match_package_ids and lowered_package_id not in {
            package_id.lower() for package_id in self.match_package_ids
        }:
            return False

        return True


def default_policy_rules() -> list[PolicyRule]:
    return [
        PolicyRule(
            name="manual-managed-codex-lb-runtime",
            match_manager="managed-runtime",
            match_names=(MANAGED_CODEX_LB_NAME,),
            update_channel="manual-only",
            risk="high",
            auto_apply=False,
            release_age_bypass=False,
            command_template=r'python "{home_dir}\.codex\scripts\update_codex_lb.py" --activate',
            skip_reason="Shared codex-lb runtime uses staged side-by-side upgrades and should switch only via the managed updater.",
        ),
        PolicyRule(
            name="manual-npm-interactive-runtime-tools",
            match_manager="npm",
            match_names=("@openai/codex", "@anthropic-ai/claude-code"),
            update_channel="manual-only",
            risk="high",
            auto_apply=False,
            release_age_bypass=False,
            command_template="npm -g update {name}",
            skip_reason="Interactive AI runtimes keep live binaries locked on Windows and must be updated in a drained maintenance window.",
        ),
        PolicyRule(
            name="manual-runtime-apps",
            match_manager="winget",
            match_package_ids=("Google.Antigravity", "Anysphere.Cursor", "Anthropic.Claude"),
            update_channel="manual-only",
            risk="high",
            command_template="winget upgrade --id {package_id} --source winget",
            skip_reason="Interactive AI runtimes stay manual to avoid silent startup regressions.",
        ),
        PolicyRule(
            name="winget-opencode-desktop",
            match_manager="winget",
            match_package_ids=("SST.OpenCodeDesktop",),
            update_channel="winget",
            risk="medium",
            auto_apply=True,
            release_age_bypass=True,
            command_template="winget upgrade --id {package_id} --source winget",
        ),
        PolicyRule(
            name="manual-powershell",
            match_manager="winget",
            match_package_ids=("Microsoft.PowerShell",),
            update_channel="manual-only",
            risk="high",
            command_template="winget upgrade Microsoft.PowerShell --source winget",
            skip_reason="PowerShell remains manual until channel and rollback policy are explicit.",
        ),
        PolicyRule(
            name="manual-local-bin-runtime-shims",
            match_manager="local-bin",
            match_names=("claude",),
            update_channel="manual-only",
            risk="high",
            command_template="manual review required for {name}",
            skip_reason="Runtime shim updates stay manual to avoid mutating active AI toolchains on login.",
        ),
        PolicyRule(
            name="local-bin-rtk",
            match_manager="local-bin",
            match_names=("rtk",),
            update_channel="github-lastversion",
            risk="medium",
            command_template="lastversion --at {install_dir} {name}",
        ),
        PolicyRule(
            name="local-bin-claude-shim",
            match_manager="local-bin",
            match_names=("claude",),
            update_channel="npm-global-shim",
            risk="medium",
            command_template="npm -g update @anthropic-ai/claude-code",
        ),
        PolicyRule(
            name="local-bin-openclaw-doctor",
            match_manager="local-bin",
            match_names=("openclaw-doctor",),
            update_channel="npm-global-shim",
            risk="medium",
            command_template="npm -g update openclaw",
        ),
        PolicyRule(
            name="local-bin-nano-pdf-shim",
            match_manager="local-bin",
            match_names=("nano-pdf",),
            update_channel="uv-tool-shim",
            risk="low",
            command_template="uv tool upgrade nano-pdf",
        ),
        PolicyRule(
            name="local-bin-uv-shim",
            match_manager="local-bin",
            match_names=("uv", "uvx", "uvw"),
            update_channel="manual-only",
            risk="medium",
            command_template="winget upgrade --id Astral-sh.uv --source winget",
            skip_reason="uv shims should be updated through the canonical installer, not replaced directly.",
        ),
        PolicyRule(
            name="local-bin-python-shim",
            match_manager="local-bin",
            match_names=("python3.11", "python3.13"),
            update_channel="manual-only",
            risk="high",
            command_template="scoop update python python311",
            skip_reason="Python shim upgrades can break global associations and should stay explicit.",
        ),
        PolicyRule(
            name="manual-npm-runtime-tools",
            match_manager="npm",
            match_names=("opencode", "clawhub"),
            update_channel="manual-only",
            risk="high",
            command_template="npm -g update {name}",
            skip_reason="AI coding runtimes and orchestrators should not auto-update during startup.",
        ),
        PolicyRule(
            name="npm-openclaw-safe",
            match_manager="npm",
            match_names=("openclaw",),
            update_channel="npm-global",
            risk="medium",
            auto_apply=True,
            command_template="npm -g update openclaw",
            skip_reason="Global openclaw package update stays outside project files and can be auto-applied by the updater.",
        ),
        PolicyRule(
            name="npm-global-default",
            match_manager="npm",
            update_channel="npm-global",
            risk="medium",
            auto_apply=True,
            command_template="npm -g update {name}",
        ),
        PolicyRule(
            name="uv-tool-default",
            match_manager="uv-tool",
            update_channel="uv-tool",
            risk="low",
            auto_apply=True,
            command_template="uv tool upgrade {name}",
        ),
        PolicyRule(
            name="scoop-default",
            match_manager="scoop",
            update_channel="scoop",
            risk="medium",
            auto_apply=True,
            command_template="scoop update {name}",
        ),
        PolicyRule(
            name="winget-default",
            match_manager="winget",
            update_channel="winget",
            risk="medium",
            auto_apply=True,
            command_template="winget upgrade --id {package_id} --source winget",
        ),
        PolicyRule(
            name="antigravity-approved-extensions",
            match_manager="antigravity-extension",
            match_names=("sst-dev.opencode", "yaumike.iflow-for-vscode"),
            update_channel="antigravity-extension-safe",
            risk="medium",
            auto_apply=True,
            release_age_bypass=True,
            command_template="antigravity --install-extension {name} --force",
        ),
        PolicyRule(
            name="antigravity-extension-default",
            match_manager="antigravity-extension",
            update_channel="extension-host",
            risk="high",
            command_template="managed by host extension marketplace",
            skip_reason="Extensions should be updated by the host marketplace, not direct file replacement.",
        ),
    ]


def _winget_needs_include_unknown(record: ToolRecord) -> bool:
    return record.manager == "winget" and (record.current_version or "").strip().lower() == "unknown"


def _finalize_recommended_command(command: str, record: ToolRecord) -> str:
    if _winget_needs_include_unknown(record) and "--include-unknown" not in command:
        return f"{command} --include-unknown"
    return command


def _winget_unknown_skip_reason() -> str:
    return (
        "WinGet cannot determine the installed version for this package; "
        "manual update is required to avoid repeated auto-reinstall loops."
    )


def classify_record(record: ToolRecord, rules: Sequence[PolicyRule]) -> ClassifiedToolRecord:
    for rule in rules:
        if rule.matches(record):
            install_dir = str(Path(record.install_path).parent) if record.install_path else ""
            command = rule.command_template.format(
                name=record.name,
                package_id=record.package_id or record.name,
                install_dir=install_dir,
                home_dir=str(Path.home()),
                current_version=record.current_version or "",
                latest_version=record.latest_version or "",
            )
            command = _finalize_recommended_command(command, record)
            update_channel = rule.update_channel
            auto_apply = rule.auto_apply
            release_age_bypass = rule.release_age_bypass
            skip_reason = rule.skip_reason
            if _winget_needs_include_unknown(record) and rule.auto_apply:
                update_channel = "manual-only"
                auto_apply = False
                release_age_bypass = False
                skip_reason = _winget_unknown_skip_reason()
            return ClassifiedToolRecord(
                **asdict(record),
                update_channel=update_channel,
                recommended_command=command,
                risk=rule.risk,
                auto_apply=auto_apply,
                release_age_bypass=release_age_bypass,
                skip_reason=skip_reason,
            )

    return ClassifiedToolRecord(
        **asdict(record),
        update_channel="unknown",
        recommended_command="manual investigation required",
        risk="high",
        auto_apply=False,
        release_age_bypass=False,
        skip_reason="No matching policy rule.",
    )


def resolve_executable(command_name: str, home: Path | None = None) -> str | None:
    resolved = shutil.which(command_name)
    if resolved:
        return resolved

    home = home or Path(os.environ.get("USERPROFILE", str(Path.home())))
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    candidates = [
        home / "scoop" / "shims" / f"{command_name}.cmd",
        home / "scoop" / "shims" / f"{command_name}.exe",
        home / ".local" / "bin" / f"{command_name}.exe",
        appdata / "npm" / f"{command_name}.cmd",
        appdata / "npm" / f"{command_name}.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _managed_codex_lb_wrapper_path(home: Path) -> Path:
    return home / MANAGED_CODEX_LB_WRAPPER_RELATIVE


def _extract_managed_codex_lb_exe(wrapper_text: str) -> Path | None:
    match = MANAGED_CODEX_LB_PIN_RE.search(wrapper_text)
    if not match:
        return None
    return Path(match.group("path"))


def _python_site_packages_for_runtime(executable_path: Path) -> Path | None:
    scripts_dir = executable_path.parent
    if scripts_dir.name.lower() != "scripts":
        return None
    venv_root = scripts_dir.parent
    site_packages = venv_root / "Lib" / "site-packages"
    return site_packages if site_packages.exists() else None


def _distribution_version_from_site_packages(site_packages: Path, package_name: str) -> str | None:
    try:
        for dist in importlib_metadata.distributions(path=[str(site_packages)]):
            name = dist.metadata.get("Name")
            if isinstance(name, str) and name.lower() == package_name.lower():
                return dist.version
    except Exception:
        return None
    return None


def _scan_managed_codex_lb(home: Path | None = None) -> ToolRecord | None:
    home = home or Path.home()
    wrapper_path = _managed_codex_lb_wrapper_path(home)
    if not wrapper_path.exists():
        return None
    wrapper_text = wrapper_path.read_text(encoding="utf-8", errors="ignore")
    executable_path = _extract_managed_codex_lb_exe(wrapper_text)
    if executable_path is None or not executable_path.exists():
        return None
    site_packages = _python_site_packages_for_runtime(executable_path)
    if site_packages is None:
        return None
    version = _distribution_version_from_site_packages(site_packages, MANAGED_CODEX_LB_NAME)
    if not version:
        return None
    return ToolRecord(
        name=MANAGED_CODEX_LB_NAME,
        current_version=version,
        source="managed-runtime",
        manager="managed-runtime",
        package_id=MANAGED_CODEX_LB_PACKAGE_ID,
        install_path=str(executable_path),
    )


def _run_command(command: Sequence[str], *, timeout_seconds: float | None = None) -> str:
    executable = resolve_executable(command[0])
    if executable is None:
        return ""
    try:
        completed = subprocess.run(
            [executable, *command[1:]],
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return ""
    return completed.stdout


def _parse_winget_list(raw_output: str) -> list[ToolRecord]:
    records: list[ToolRecord] = []
    for line in raw_output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("Name ", "---", "█", "▒")):
            continue
        if stripped.startswith(("|", "/", "\\", "-")) and " " not in stripped:
            continue
        match = re.match(r"^(?P<name>.+?)\s{2,}(?P<package_id>\S+)\s{2,}(?P<rest>.+)$", stripped)
        if not match:
            continue
        rest_tokens = re.split(r"\s{2,}", match.group("rest").strip())
        if not rest_tokens:
            continue
        version = rest_tokens[0]
        available = None
        source = "winget"
        if len(rest_tokens) >= 2:
            if rest_tokens[-1] in KNOWN_WINGET_SOURCES:
                source = rest_tokens[-1]
                if len(rest_tokens) >= 3:
                    available = rest_tokens[1]
            else:
                available = rest_tokens[1]
        records.append(
            ToolRecord(
                name=match.group("name").strip(),
                current_version=version,
                latest_version=available,
                source=source,
                manager="winget",
                package_id=match.group("package_id"),
            )
        )
    return records


def _parse_scoop_list(raw_output: str) -> list[ToolRecord]:
    records: list[ToolRecord] = []
    for line in raw_output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("Installed apps:", "Name", "----")):
            continue
        parts = re.split(r"\s{2,}", stripped)
        if len(parts) < 3:
            continue
        name = parts[0]
        version = parts[1]
        source = parts[2]
        records.append(
            ToolRecord(
                name=name,
                current_version=version,
                source=source,
                manager="scoop",
            )
        )
    return records


def _parse_uv_tools(raw_output: str) -> list[ToolRecord]:
    records: list[ToolRecord] = []
    for line in raw_output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("- "):
            continue
        match = re.match(r"^(?P<name>.+?) v(?P<version>\S+)$", stripped)
        if match:
            records.append(
                ToolRecord(
                    name=match.group("name"),
                    current_version=match.group("version"),
                    source="uv",
                    manager="uv-tool",
                )
            )
    return records


def _parse_npm_global(raw_output: str) -> list[ToolRecord]:
    records: list[ToolRecord] = []
    for line in raw_output.splitlines():
        stripped = line.rstrip()
        if "@@" not in stripped and "@" not in stripped:
            continue
        match = re.search(r"(?P<name>@?[^@\s]+(?:/[^@\s]+)?)@(?P<version>[^\s]+)$", stripped)
        if match:
            records.append(
                ToolRecord(
                    name=match.group("name"),
                    current_version=match.group("version"),
                    source="npm-global",
                    manager="npm",
                )
            )
    return records


def _parse_winget_upgrade(raw_output: str) -> dict[str, str]:
    latest_by_id: dict[str, str] = {}
    for line in raw_output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("Name ", "---", "█", "▒")):
            continue
        match = re.match(
            r"^(?P<name>.+?)\s{2,}(?P<package_id>\S+)\s{2,}(?P<version>\S+)\s{2,}(?P<available>\S+)\s{2,}(?P<source>\S+)$",
            stripped,
        )
        if not match:
            continue
        latest_by_id[match.group("package_id")] = match.group("available")
    return latest_by_id


def _parse_npm_outdated_json(raw_output: str) -> dict[str, str]:
    if not raw_output.strip():
        return {}
    data = json.loads(raw_output)
    latest_by_name: dict[str, str] = {}
    for package_name, payload in data.items():
        latest = payload.get("latest")
        if isinstance(latest, str) and latest:
            latest_by_name[package_name] = latest
    return latest_by_name


def latest_from_pypi_payload(payload: str) -> str | None:
    if not payload.strip():
        return None
    data = json.loads(payload)
    version = data.get("info", {}).get("version")
    return version if isinstance(version, str) and version else None


def latest_from_github_release_payload(payload: str) -> str | None:
    if not payload.strip():
        return None
    data = json.loads(payload)
    version = data.get("tag_name") or data.get("name")
    if not isinstance(version, str) or not version:
        return None
    return version[1:] if version.startswith("v") else version


def github_release_info_from_payload(payload: str) -> tuple[str | None, str | None]:
    if not payload.strip():
        return None, None
    data = json.loads(payload)
    version = data.get("tag_name") or data.get("name")
    published_at = data.get("published_at")
    if not isinstance(version, str) or not version:
        return None, None
    if not isinstance(published_at, str) or not published_at:
        return None, None
    return (version[1:] if version.startswith("v") else version), published_at


def npm_publish_time_from_payload(payload: str, version: str) -> str | None:
    if not payload.strip() or not version:
        return None
    data = json.loads(payload)
    time_map = data.get("time")
    if not isinstance(time_map, dict):
        return None
    published_at = time_map.get(version)
    return published_at if isinstance(published_at, str) and published_at else None


def latest_from_npm_registry_payload(payload: str) -> str | None:
    if not payload.strip():
        return None
    data = json.loads(payload)
    dist_tags = data.get("dist-tags")
    if not isinstance(dist_tags, dict):
        return None
    latest = dist_tags.get("latest")
    return latest if isinstance(latest, str) and latest else None


def pypi_publish_time_from_payload(payload: str, version: str) -> str | None:
    if not payload.strip() or not version:
        return None
    data = json.loads(payload)
    releases = data.get("releases")
    if not isinstance(releases, dict):
        return None
    files = releases.get(version)
    if not isinstance(files, list):
        return None
    for file_info in files:
        if not isinstance(file_info, dict):
            continue
        published_at = file_info.get("upload_time_iso_8601") or file_info.get("upload_time")
        if isinstance(published_at, str) and published_at:
            return published_at
    return None


def latest_from_scoop_manifest_payload(payload: str) -> str | None:
    if not payload.strip():
        return None
    data = json.loads(payload)
    version = data.get("version")
    return version if isinstance(version, str) and version else None


def _scan_local_bin(bin_dir: Path) -> list[ToolRecord]:
    records: list[ToolRecord] = []
    if not bin_dir.exists():
        return records
    for child in sorted(bin_dir.iterdir()):
        if child.is_file() and child.suffix.lower() in {".exe", ".cmd", ".bat"}:
            records.append(
                ToolRecord(
                    name=child.stem,
                    source="local-bin",
                    manager="local-bin",
                    install_path=str(child),
                )
            )
    return records


def _split_extension_name(folder_name: str) -> tuple[str, str | None]:
    match = re.match(r"^(?P<name>.+)-(?P<version>\d[\w.\-]*)$", folder_name)
    if not match:
        return folder_name, None
    return match.group("name"), match.group("version")


def _scan_antigravity_extensions(extension_dir: Path) -> list[ToolRecord]:
    records: list[ToolRecord] = []
    if not extension_dir.exists():
        return records
    for child in sorted(extension_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        name, version = _split_extension_name(child.name)
        records.append(
            ToolRecord(
                name=name,
                current_version=version,
                source="antigravity-extension",
                manager="antigravity-extension",
                install_path=str(child),
            )
        )
    return records


def enrich_latest_versions(
    records: Sequence[ToolRecord],
    *,
    winget_latest: dict[str, str] | None = None,
    npm_latest: dict[str, str] | None = None,
    pypi_latest: dict[str, str] | None = None,
    github_latest: dict[str, str] | None = None,
    scoop_latest: dict[str, str] | None = None,
    npm_metadata: dict[str, ReleaseMetadata] | None = None,
    pypi_metadata: dict[str, ReleaseMetadata] | None = None,
    github_metadata: dict[str, ReleaseMetadata] | None = None,
) -> list[ToolRecord]:
    winget_latest = winget_latest or {}
    npm_latest = npm_latest or {}
    pypi_latest = pypi_latest or {}
    github_latest = github_latest or {}
    scoop_latest = scoop_latest or {}
    npm_metadata = npm_metadata or {}
    pypi_metadata = pypi_metadata or {}
    github_metadata = github_metadata or {}
    enriched: list[ToolRecord] = []

    for record in records:
        latest = record.latest_version
        published_at = record.release_published_at
        release_source = record.release_date_source
        if record.manager == "winget":
            latest = latest or winget_latest.get(record.package_id or "")
        elif record.manager == "npm":
            metadata = npm_metadata.get(record.name)
            if metadata:
                latest = latest or metadata.version
                published_at = published_at or metadata.published_at
                release_source = release_source or metadata.source
            else:
                latest = latest or npm_latest.get(record.name)
        elif record.manager in {"uv-tool", "managed-runtime"}:
            metadata = pypi_metadata.get(record.name)
            if metadata:
                latest = latest or metadata.version
                published_at = published_at or metadata.published_at
                release_source = release_source or metadata.source
            else:
                latest = latest or pypi_latest.get(record.name)
        elif record.manager == "local-bin":
            metadata = github_metadata.get(record.name)
            if metadata:
                latest = latest or metadata.version
                published_at = published_at or metadata.published_at
                release_source = release_source or metadata.source
            else:
                latest = latest or github_latest.get(record.name)
        elif record.manager == "scoop":
            latest = latest or scoop_latest.get(record.name)

        payload = asdict(record)
        payload["latest_version"] = latest
        payload["release_published_at"] = published_at
        payload["release_date_source"] = release_source
        enriched.append(
            ToolRecord(**payload)
        )

    return enriched


def _fetch_text(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "ToolUpdateAuditor/0.1 (+https://local.machine)",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError):
        return ""


def _parse_utc_timestamp(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc)


def _format_utc_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _release_age_identity_value(record: ClassifiedToolRecord) -> str:
    if record.package_id:
        return record.package_id
    return record.name


def release_age_state_key(record: ClassifiedToolRecord) -> str:
    return json.dumps(
        [
            record.manager or "",
            _release_age_identity_value(record),
            record.current_version or "",
            record.latest_version or "",
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )


def load_release_age_first_seen(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    first_seen_by_update = payload.get("first_seen_by_update", {})
    if not isinstance(first_seen_by_update, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in first_seen_by_update.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def save_release_age_first_seen(path: Path, first_seen_by_update: Mapping[str, str]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "first_seen_by_update": dict(sorted(first_seen_by_update.items())),
            },
            indent=2,
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )


def apply_release_age_policy(
    records: Sequence[ClassifiedToolRecord],
    *,
    now_utc: datetime | None = None,
    first_seen_by_update: dict[str, str] | None = None,
) -> list[ClassifiedToolRecord]:
    now_utc = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    updated: list[ClassifiedToolRecord] = []
    first_seen_by_update = first_seen_by_update if first_seen_by_update is not None else {}
    active_fallback_keys: set[str] = set()

    for record in records:
        payload = asdict(record)
        if not record.latest_version or not record.current_version or record.latest_version == record.current_version:
            payload["age_gate_status"] = "not-applicable"
            payload["age_gate_reason"] = None
            payload["eligible_after"] = None
            updated.append(ClassifiedToolRecord(**payload))
            continue

        if record.release_age_bypass:
            payload["age_gate_status"] = "ready"
            payload["age_gate_reason"] = None
            payload["eligible_after"] = None
            updated.append(ClassifiedToolRecord(**payload))
            continue

        published_at = _parse_utc_timestamp(record.release_published_at)
        if published_at is None:
            if record.auto_apply and record.update_channel in SAFE_UPDATE_CHANNELS:
                state_key = release_age_state_key(record)
                first_seen_at = _parse_utc_timestamp(first_seen_by_update.get(state_key)) or now_utc
                first_seen_by_update[state_key] = _format_utc_timestamp(first_seen_at) or _format_utc_timestamp(now_utc) or ""
                active_fallback_keys.add(state_key)
                eligible_after = first_seen_at + timedelta(hours=72)
                payload["eligible_after"] = _format_utc_timestamp(eligible_after)
                if now_utc < eligible_after:
                    payload["age_gate_status"] = "deferred"
                    payload["age_gate_reason"] = "waiting 72 hours from first detection"
                else:
                    payload["age_gate_status"] = "ready"
                    payload["age_gate_reason"] = None
            else:
                payload["age_gate_status"] = "unknown"
                payload["age_gate_reason"] = "publish timestamp unavailable"
                payload["eligible_after"] = None
            updated.append(ClassifiedToolRecord(**payload))
            continue

        eligible_after = published_at + timedelta(hours=72)
        payload["eligible_after"] = _format_utc_timestamp(eligible_after)
        if now_utc < eligible_after:
            payload["age_gate_status"] = "deferred"
            payload["age_gate_reason"] = "release younger than 72 hours"
        else:
            payload["age_gate_status"] = "ready"
            payload["age_gate_reason"] = None
        updated.append(ClassifiedToolRecord(**payload))

    stale_keys = [key for key in list(first_seen_by_update) if key not in active_fallback_keys]
    for key in stale_keys:
        first_seen_by_update.pop(key, None)

    return updated


def collect_winget_latest() -> dict[str, str]:
    return _parse_winget_upgrade(_run_command(["winget", "upgrade"]))


def collect_npm_latest() -> dict[str, str]:
    return _parse_npm_outdated_json(_run_command(["npm", "outdated", "-g", "--json"], timeout_seconds=15))


def collect_npm_metadata(
    records: Sequence[ToolRecord],
    npm_latest: dict[str, str] | None = None,
) -> dict[str, ReleaseMetadata]:
    npm_latest = npm_latest or {}
    metadata: dict[str, ReleaseMetadata] = {}
    for record in records:
        if record.manager != "npm":
            continue
        payload = _fetch_text(f"https://registry.npmjs.org/{quote(record.name, safe='@/')}")
        version = latest_from_npm_registry_payload(payload) or npm_latest.get(record.name) or record.latest_version
        if not version:
            continue
        published_at = npm_publish_time_from_payload(payload, version)
        metadata[record.name] = ReleaseMetadata(version=version, published_at=published_at, source="npm-registry")
    return metadata


def collect_pypi_latest(records: Sequence[ToolRecord]) -> dict[str, str]:
    return {name: metadata.version for name, metadata in collect_pypi_metadata(records).items()}


def collect_pypi_metadata(records: Sequence[ToolRecord]) -> dict[str, ReleaseMetadata]:
    metadata: dict[str, ReleaseMetadata] = {}
    for record in records:
        if record.manager not in {"uv-tool", "managed-runtime"}:
            continue
        payload = _fetch_text(f"https://pypi.org/pypi/{record.name}/json")
        version = latest_from_pypi_payload(payload)
        if version:
            metadata[record.name] = ReleaseMetadata(
                version=version,
                published_at=pypi_publish_time_from_payload(payload, version),
                source="pypi-json",
            )
    return metadata


def collect_github_latest(records: Sequence[ToolRecord]) -> dict[str, str]:
    return {name: metadata.version for name, metadata in collect_github_metadata(records).items()}


def collect_github_metadata(records: Sequence[ToolRecord]) -> dict[str, ReleaseMetadata]:
    repo_map = {
        "rtk": "rtk-ai/rtk",
    }
    metadata: dict[str, ReleaseMetadata] = {}
    for record in records:
        repo = repo_map.get(record.name)
        if not repo:
            continue
        payload = _fetch_text(f"https://api.github.com/repos/{repo}/releases/latest")
        version, published_at = github_release_info_from_payload(payload)
        if version:
            metadata[record.name] = ReleaseMetadata(
                version=version,
                published_at=published_at,
                source="github-release",
            )
    return metadata


def pypi_metadata_enabled(env: Mapping[str, str] | None = None) -> bool:
    env = env or os.environ
    enabled = env.get("TOOL_AUDIT_ENABLE_PYPI")
    if enabled is not None and enabled != "":
        return enabled.lower() in {"1", "true", "yes"}
    return env.get("TOOL_AUDIT_DISABLE_PYPI", "").lower() not in {"1", "true", "yes"}


def default_bucket_raw_bases(remotes_by_bucket: dict[str, str]) -> dict[str, list[str]]:
    bases: dict[str, list[str]] = {}
    for bucket_name, remote_url in remotes_by_bucket.items():
        cleaned = remote_url.removesuffix(".git")
        match = re.match(r"https://github.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)$", cleaned)
        if not match:
            continue
        owner = match.group("owner")
        repo = match.group("repo")
        bases[bucket_name] = [
            f"https://raw.githubusercontent.com/{owner}/{repo}/master/bucket",
            f"https://raw.githubusercontent.com/{owner}/{repo}/main/bucket",
        ]
    return bases


def collect_scoop_bucket_remotes(home: Path | None = None) -> dict[str, str]:
    home = home or Path.home()
    bucket_root = home / "scoop" / "buckets"
    remotes: dict[str, str] = {}
    if not bucket_root.exists():
        return remotes
    for child in bucket_root.iterdir():
        if not child.is_dir():
            continue
        output = _run_command(["git", "-C", str(child), "remote", "get-url", "origin"]).strip()
        if output:
            remotes[child.name] = output
    return remotes


def collect_scoop_latest(
    records: Sequence[ToolRecord],
    bucket_raw_bases: dict[str, list[str]] | None = None,
) -> dict[str, str]:
    bucket_raw_bases = bucket_raw_bases or {}
    latest: dict[str, str] = {}
    for record in records:
        if record.manager != "scoop" or not record.source:
            continue
        for base in bucket_raw_bases.get(record.source, []):
            payload = _fetch_text(f"{base}/{record.name}.json")
            version = latest_from_scoop_manifest_payload(payload)
            if version:
                latest[record.name] = version
                break
    return latest


def collect_inventory(home: Path | None = None) -> list[ToolRecord]:
    home = home or Path.home()
    records: list[ToolRecord] = []
    records.extend(_parse_winget_list(_run_command(["winget", "list"])))
    records.extend(_parse_scoop_list(_run_command(["scoop", "list"])))
    records.extend(_parse_uv_tools(_run_command(["uv", "tool", "list"])))
    records.extend(_parse_npm_global(_run_command(["npm", "-g", "list", "--depth=0"])))
    records.extend(_scan_local_bin(home / ".local" / "bin"))
    records.extend(_scan_antigravity_extensions(home / ".antigravity" / "extensions"))
    managed_codex_lb = _scan_managed_codex_lb(home)
    if managed_codex_lb is not None:
        records.append(managed_codex_lb)
    return records


def dedupe_records(records: Sequence[ToolRecord]) -> list[ToolRecord]:
    seen: set[tuple[str, str]] = set()
    deduped: list[ToolRecord] = []
    for record in records:
        key = (record.manager or "", record.name.lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def build_safe_update_plan(records: Sequence[ClassifiedToolRecord]) -> list[ClassifiedToolRecord]:
    plan = [
        record
        for record in records
        if record.auto_apply
        if record.update_channel in SAFE_UPDATE_CHANNELS
        and record.latest_version
        and record.current_version
        and record.latest_version != record.current_version
        and record.age_gate_status == "ready"
    ]
    return sorted(plan, key=lambda item: item.name.lower())


def render_markdown_report(records: Sequence[ClassifiedToolRecord]) -> str:
    channel_counts: dict[str, int] = {}
    for record in records:
        channel_counts[record.update_channel] = channel_counts.get(record.update_channel, 0) + 1

    lines = [
        "# Tool Update Audit",
        "",
        "## Summary",
        "",
        "| Update channel | Count |",
        "| --- | ---: |",
    ]
    for channel, count in sorted(channel_counts.items()):
        lines.append(f"| {channel} | {count} |")

    lines.extend(
        [
            "",
            "## Tools",
            "",
            "| Name | Current | Latest | Published | Eligible after | Age status | Manager | Channel | Risk | Auto apply | Command |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for record in sorted(records, key=lambda item: (item.update_channel, item.name.lower())):
        lines.append(
            f"| {record.name} | {record.current_version or '-'} | {record.latest_version or '-'} | "
            f"{record.release_published_at or '-'} | {record.eligible_after or '-'} | {record.age_gate_status or '-'} | "
            f"{record.manager or '-'} | {record.update_channel} | {record.risk} | "
            f"{'yes' if record.auto_apply else 'no'} | {record.recommended_command or '-'} |"
        )
    return "\n".join(lines) + "\n"


def render_updates_only_markdown_report(records: Sequence[ClassifiedToolRecord]) -> str:
    update_records = [
        record
        for record in records
        if record.latest_version
        and record.current_version
        and record.latest_version != record.current_version
    ]
    lines = [
        "# Tool Updates Only",
        "",
        "| Name | Current | Latest | Published | Eligible after | Age status | Manager | Channel | Command |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for record in sorted(update_records, key=lambda item: item.name.lower()):
        lines.append(
            f"| {record.name} | {record.current_version} | {record.latest_version} | "
            f"{record.release_published_at or '-'} | {record.eligible_after or '-'} | {record.age_gate_status or '-'} | "
            f"{record.manager or '-'} | {record.update_channel} | {record.recommended_command or '-'} |"
        )
    return "\n".join(lines) + "\n"


def write_reports(output_dir: Path, records: Sequence[ClassifiedToolRecord]) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "tool-update-audit.json"
    md_path = output_dir / "tool-update-audit.md"
    updates_md_path = output_dir / "tool-updates-only.md"
    json_path.write_text(
        json.dumps([asdict(record) for record in records], indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown_report(records), encoding="utf-8")
    updates_md_path.write_text(render_updates_only_markdown_report(records), encoding="utf-8")
    return json_path, md_path, updates_md_path


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    inventory = dedupe_records(collect_inventory())
    enable_pypi = pypi_metadata_enabled()
    scoop_bucket_bases = default_bucket_raw_bases(collect_scoop_bucket_remotes())
    release_age_state_path = base_dir / "artifacts" / RELEASE_AGE_FIRST_SEEN_FILE_NAME
    first_seen_by_update = load_release_age_first_seen(release_age_state_path)
    npm_latest = collect_npm_latest()
    npm_metadata = collect_npm_metadata(inventory, npm_latest)
    pypi_metadata = collect_pypi_metadata(inventory) if enable_pypi else {}
    github_metadata = collect_github_metadata(inventory)
    inventory = enrich_latest_versions(
        inventory,
        winget_latest=collect_winget_latest(),
        npm_latest=npm_latest,
        pypi_latest={name: metadata.version for name, metadata in pypi_metadata.items()},
        github_latest={name: metadata.version for name, metadata in github_metadata.items()},
        scoop_latest=collect_scoop_latest(inventory, scoop_bucket_bases),
        npm_metadata=npm_metadata,
        pypi_metadata=pypi_metadata,
        github_metadata=github_metadata,
    )
    classified = [classify_record(record, default_policy_rules()) for record in inventory]
    classified = apply_release_age_policy(classified, first_seen_by_update=first_seen_by_update)
    release_age_state_path.parent.mkdir(parents=True, exist_ok=True)
    save_release_age_first_seen(release_age_state_path, first_seen_by_update)
    json_path, md_path, updates_md_path = write_reports(base_dir / "artifacts", classified)
    print(f"Wrote audit JSON to {json_path}")
    print(f"Wrote audit Markdown to {md_path}")
    print(f"Wrote updates-only Markdown to {updates_md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
