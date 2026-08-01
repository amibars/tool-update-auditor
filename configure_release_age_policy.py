from __future__ import annotations

import argparse
import os
from pathlib import Path


def _ensure_trailing_newline(text: str) -> str:
    return text if not text or text.endswith("\n") else f"{text}\n"


def upsert_line_setting(text: str, key: str, value: str) -> str:
    lines = _ensure_trailing_newline(text).splitlines()
    needle = f"{key}="
    replaced = False
    updated: list[str] = []
    for line in lines:
        if line.startswith(needle):
            updated.append(f"{key}={value}")
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(f"{key}={value}")
    return "\n".join(updated).rstrip("\n") + "\n"


def upsert_uv_setting(text: str, key: str, value: str) -> str:
    lines = _ensure_trailing_newline(text).splitlines()
    needle = f"{key} = "
    replaced = False
    updated: list[str] = []
    for line in lines:
        if line.startswith(needle):
            updated.append(f'{key} = "{value}"')
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(f'{key} = "{value}"')
    return "\n".join(updated).rstrip("\n") + "\n"


def build_release_age_policy_files(
    *,
    home: Path,
    appdata: Path,
    localappdata: Path,
    existing_files: dict[Path, str] | None = None,
) -> dict[Path, str]:
    existing_files = existing_files or {}
    npmrc = home / ".npmrc"
    uv_toml = appdata / "uv" / "uv.toml"
    pnpm_rc = localappdata / "pnpm" / "config" / "rc"

    return {
        npmrc: upsert_line_setting(existing_files.get(npmrc, ""), "min-release-age", "3"),
        uv_toml: upsert_uv_setting(existing_files.get(uv_toml, ""), "exclude-newer", "72 hours"),
        pnpm_rc: upsert_line_setting(existing_files.get(pnpm_rc, ""), "minimumReleaseAge", "4320"),
    }


def _default_existing_files(paths: list[Path]) -> dict[Path, str]:
    payload: dict[Path, str] = {}
    for path in paths:
        if path.exists():
            payload[path] = path.read_text(encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview or apply 72-hour release-age policy config for npm, uv, and pnpm.")
    parser.add_argument("--apply", action="store_true", help="Write the config files instead of printing the planned updates.")
    args = parser.parse_args()

    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    localappdata = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))

    target_paths = [
        home / ".npmrc",
        appdata / "uv" / "uv.toml",
        localappdata / "pnpm" / "config" / "rc",
    ]
    updates = build_release_age_policy_files(
        home=home,
        appdata=appdata,
        localappdata=localappdata,
        existing_files=_default_existing_files(target_paths),
    )

    for path, content in updates.items():
        print(path)
        print(content.rstrip())
        print("")
        if args.apply:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    if not args.apply:
        print("Dry run only. Re-run with --apply to persist the policy files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
