from __future__ import annotations

import getpass
import os
from pathlib import Path
import sys


def resolve_windows_user_home(
    *,
    home: Path | None = None,
    userprofile: str | None = None,
    username: str | None = None,
    windows_users_root: Path = Path("C:/Users"),
) -> Path:
    username = username or getpass.getuser()
    candidates: list[Path] = []
    if userprofile:
        candidates.append(Path(userprofile))
    candidates.append(windows_users_root / username)
    candidates.append(home or Path.home())

    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(candidate).lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        if candidate.name == ".codex-store-userhome":
            continue
        if (candidate / "AppData" / "Roaming").exists():
            return candidate

    return home or Path.home()


def startup_dir() -> Path:
    user_home = resolve_windows_user_home(
        home=Path.home(),
        userprofile=os.environ.get("USERPROFILE"),
        username=getpass.getuser(),
    )
    return user_home / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def choose_python_launcher(python_exe: str) -> str:
    python_path = Path(python_exe)
    pythonw_path = python_path.with_name("pythonw.exe")
    if pythonw_path.exists():
        return str(pythonw_path)
    return python_exe


def launcher_content(base_dir: Path, python_exe: str) -> str:
    return f'@echo off\r\n"{python_exe}" "{base_dir / "startup_runner.py"}"\r\n'


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    startup = startup_dir()
    startup.mkdir(parents=True, exist_ok=True)
    launcher = startup / "ToolUpdateAuditor.cmd"
    launcher.write_text(
        launcher_content(base_dir, choose_python_launcher(sys.executable)),
        encoding="utf-8",
    )
    print(f"Installed startup launcher at {launcher}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
