# ToolUpdateAuditor

Windows workstation inventory and update-policy auditor for `winget`, Scoop,
global npm packages, `uv tool`, local command shims, and Antigravity extensions.

It is an **audit-first** tool. It writes local JSON and Markdown reports, adds a
release-age gate where upstream metadata is available, and prints the exact
commands needed to apply policy-approved updates.

## Safety model

- Installing the logon or weekly task performs an audit only. It does not apply
  updates.
- Applying updates always requires an explicit `--execute` or `--apply-ready`
  command.
- Commands are executed as argument arrays, not through `shell=True`.
- Generated inventory, reports, caches, scheduled-launcher files, and local
  configuration are ignored by Git.
- Treat package-manager output as local machine data. Do not commit `artifacts/`.

## Requirements

- Windows 10/11
- Python 3.11+
- Any of the supported package managers that you want audited: `winget`, Scoop,
  npm, or `uv`

The project uses only the Python standard library at runtime. Tests use pytest.

## Usage

```powershell
python .\update_audit.py
python .\render_updates_only.py
python .\apply_safe_updates.py
```

The third command is a dry run. To apply only policy-approved, release-age-ready
updates, use:

```powershell
python .\apply_safe_updates.py --execute
```

To run an inventory cycle and write the startup report:

```powershell
python .\startup_runner.py
```

`startup_runner.py` audits only by default. Explicitly apply ready updates with:

```powershell
python .\startup_runner.py --apply-ready
```

To preview the logon and weekly audit tasks:

```powershell
python .\scheduler_install.py
```

Register them only after inspecting the output:

```powershell
python .\scheduler_install.py --execute
```

The Startup-folder launcher is also opt-in:

```powershell
python .\install_startup_runner.py
```

## Release-age policy

The optional configuration helper previews a 72-hour minimum release age for
npm, uv, and pnpm. It modifies user-level package-manager configuration only
when run with `--apply`:

```powershell
python .\configure_release_age_policy.py
python .\configure_release_age_policy.py --apply
```

## Tests

```powershell
python -m pytest -q
```

## Scope

This project is a workstation utility, not an enterprise patch-management
system. Review the generated update plan before using `--execute`, especially
for editors, drivers, security tools, or software with active processes.
