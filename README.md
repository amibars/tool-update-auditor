# ToolUpdateAuditor

Windows workstation inventory and update-policy auditor for `winget`, Scoop,
global npm packages, `uv tool`, local command shims, and Antigravity extensions.

It is an **audit-first** tool. It writes local JSON and Markdown reports, adds a
release-age gate where upstream metadata is available, and prints the exact
commands needed to apply policy-approved updates.

## Coverage and distinction

The inventory covers six workstation update surfaces in one report:

- WinGet packages;
- Scoop packages and their bucket manifests;
- global npm packages;
- `uv tool` packages from PyPI;
- local command shims in `%USERPROFILE%\.local\bin`;
- Antigravity / VS Code-compatible extensions.

Version availability is resolved through the owner appropriate to each source:
WinGet, Scoop manifests, the npm registry, PyPI, and selected GitHub releases.
Extensions remain inventory-only and are delegated to their host marketplace;
the tool never replaces extension files directly. Local shims are likewise
reported, and only explicitly mapped owners receive an update recommendation.

For update candidates with trustworthy publish metadata, the tool applies a
72-hour release-age gate. When publish metadata is unavailable for a
policy-approved channel, it can defer eligibility for 72 hours from first
detection instead. This reduces the chance of immediately installing a newly
published compromised or broken release; it is a supply-chain delay, not an
antivirus or a guarantee that an update is safe.

## Safety model

- Installing the logon or weekly task performs an audit only by default.
- Automatic application is an explicit opt-in and runs only policy-approved
  updates that have passed the release-age gate.
- Manual application requires an explicit `--execute` or `--apply-ready`
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

## One-command Windows setup

After cloning or downloading the repository, open PowerShell in the project
folder and run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

This one-time setup registers current-user tasks for logon and weekly runs.
Each run discovers the supported tools currently installed on the machine,
keeps the 72-hour release-age state locally, and automatically applies only
policy-approved updates that are eligible. No package list needs to be edited.

Use audit-only mode instead:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -AuditOnly
```

Remove both scheduled tasks:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall
```

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

To make the scheduled logon and weekly runs automatically apply only
policy-approved updates that are already eligible under the release-age gate,
enable that mode explicitly during installation:

```powershell
python .\scheduler_install.py --execute --enable-auto-apply
```

After this one-time setup, the runner inventories the currently installed tools
on each scheduled run. No static package list is required. An update with a
known publish date becomes eligible once it is 72 hours old; for a safe channel
without publish metadata, eligibility begins 72 hours after first detection.

The Startup-folder launcher is also opt-in:

```powershell
python .\install_startup_runner.py
```

Use `python .\install_startup_runner.py --enable-auto-apply` to enable the
same opt-in automatic-apply behavior for the Startup-folder launcher.

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
