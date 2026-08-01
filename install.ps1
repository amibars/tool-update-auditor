[CmdletBinding()]
param(
    [switch]$AuditOnly,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$taskNames = @(
    'ToolUpdateAuditor-AuditOnLogon',
    'ToolUpdateAuditor-AuditWeekly'
)

if ($Uninstall) {
    foreach ($taskName in $taskNames) {
        & schtasks.exe /Delete /TN $taskName /F 2>$null | Out-Null
    }
    Write-Host 'ToolUpdateAuditor scheduled tasks removed.'
    exit 0
}

$repoRoot = Split-Path -Parent $PSCommandPath
$scheduler = Join-Path $repoRoot 'scheduler_install.py'
if (-not (Test-Path -LiteralPath $scheduler)) {
    throw "scheduler_install.py was not found in $repoRoot"
}

$python = Get-Command python.exe -ErrorAction SilentlyContinue
$pythonArgs = @()
if (-not $python) {
    $python = Get-Command py.exe -ErrorAction SilentlyContinue
    $pythonArgs = @('-3')
}
if (-not $python) {
    throw 'Python 3.11 or newer is required. Install Python, reopen PowerShell, then run install.ps1 again.'
}

$pythonArgs += @($scheduler, '--execute')
if (-not $AuditOnly) {
    $pythonArgs += '--enable-auto-apply'
}

& $python.Source @pythonArgs
if ($LASTEXITCODE -ne 0) {
    throw "ToolUpdateAuditor setup failed with exit code $LASTEXITCODE"
}

if ($AuditOnly) {
    Write-Host 'Installed audit-only scheduled tasks.'
} else {
    Write-Host 'Installed automatic update tasks. At each logon, eligible policy-approved updates are applied after the release-age gate.'
}
