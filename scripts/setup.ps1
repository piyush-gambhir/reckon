#Requires -Version 5.1
<#
.SYNOPSIS
    rca-assist setup for Windows (native PowerShell).

.DESCRIPTION
    Installs every CLI that has a working native Windows port via winget,
    plus the custom Go-based CLIs. Tools without good native Windows support
    (direnv, kcat, rpk) are skipped with a clear message — for the full
    experience use WSL2 + scripts/setup.sh.

    Idempotent: re-running only installs what's missing.

.EXAMPLE
    PS> .\scripts\setup.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$Script:Installed = 0
$Script:Already   = 0
$Script:Failed    = 0
$Script:Skipped   = 0

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

function Write-Ok      { param($Msg) Write-Host "  ✓ $Msg" -ForegroundColor Green }
function Write-Info    { param($Msg) Write-Host "  → $Msg" -ForegroundColor Blue }
function Write-Warn    { param($Msg) Write-Host "  ⚠ $Msg" -ForegroundColor Yellow }
function Write-Err     { param($Msg) Write-Host "  ✗ $Msg" -ForegroundColor Red }
function Write-Header  { param($Msg) Write-Host "`n$Msg" -ForegroundColor White -BackgroundColor DarkGray }

function Test-Command {
    param([string]$Name)
    [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Mark-Installed { param($Name) Write-Ok "$Name — installed";          $Script:Installed++ }
function Mark-Already   { param($Name) Write-Ok "$Name — already installed";  $Script:Already++ }
function Mark-Failed    { param($Name) Write-Err "$Name — install failed";    $Script:Failed++ }
function Mark-Skipped   {
    param($Name, $Reason)
    Write-Warn "$Name — skipped: $Reason"
    $Script:Skipped++
}

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

function Test-Preflight {
    Write-Header 'Pre-flight'

    $os = [System.Environment]::OSVersion.Platform
    if ($os -ne 'Win32NT') {
        Write-Err "This script is for Windows. On macOS/Linux use scripts/setup.sh."
        exit 1
    }
    Write-Ok ("Windows {0}" -f [System.Environment]::OSVersion.Version)

    if (-not (Test-Command winget)) {
        Write-Err 'winget is required but not installed.'
        Write-Err 'Install "App Installer" from the Microsoft Store, then re-run.'
        exit 1
    }
    Write-Ok ("winget {0}" -f (winget --version))

    Write-Warn 'Native Windows support is partial. Tools NOT installed natively:'
    Write-Warn '  - direnv  (no maintained Windows port)'
    Write-Warn '  - kcat    (no Windows binary)'
    Write-Warn '  - rpk     (no Windows binary)'
    Write-Warn 'For the full experience, use WSL2 + scripts/setup.sh.'
    Write-Warn 'After install, dot-source scripts/activate.ps1 each session to load .env.'
}

# ---------------------------------------------------------------------------
# Install helpers
# ---------------------------------------------------------------------------

function Install-Winget {
    param(
        [Parameter(Mandatory)] [string] $Id,
        [Parameter(Mandatory)] [string] $Bin,
        [string] $DisplayName = $Bin
    )
    if (Test-Command $Bin) {
        Mark-Already $DisplayName
        return
    }
    Write-Info "$DisplayName — installing via winget ($Id)..."
    try {
        # Native stderr lines become ErrorRecords under EAP=Stop on Windows
        # PowerShell 5.1 (winget prints progress to stderr), which would throw
        # NativeCommandError mid-install. Force Continue around the native call
        # and rely on $LASTEXITCODE for success/failure.
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        winget install --id $Id `
            --silent `
            --accept-source-agreements `
            --accept-package-agreements `
            --source winget 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP
        # winget exits 0 for new install, -1978335189 if already installed.
        if ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq -1978335189) {
            Mark-Installed $DisplayName
            # Refresh PATH for the current session so the new bin is callable.
            $env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + `
                        [System.Environment]::GetEnvironmentVariable('Path', 'User')
        } else {
            Mark-Failed $DisplayName
        }
    } catch {
        Mark-Failed $DisplayName
    }
}

function Install-GoCli {
    param(
        [Parameter(Mandatory)] [string] $Module,
        [Parameter(Mandatory)] [string] $Bin
    )
    if (Test-Command $Bin) { Mark-Already $Bin; return }
    if (-not (Test-Command go)) {
        Mark-Failed "$Bin (go not installed)"
        return
    }
    Write-Info "$Bin — installing via go install ($Module)..."
    try {
        # `go install` always writes "go: downloading ..." to stderr on a fresh
        # fetch; under EAP=Stop on Windows PowerShell 5.1 that stderr is wrapped
        # in an ErrorRecord and throws, falsely failing the install. Force
        # Continue around the native call and check $LASTEXITCODE only.
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & go install "$Module@latest" 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP
        if ($LASTEXITCODE -eq 0) {
            $gobin = (& go env GOPATH).Trim() + '\bin'
            $env:Path = "$gobin;$env:Path"
            if (Test-Command $Bin) {
                Mark-Installed $Bin
            } else {
                Write-Warn "$Bin — installed to $gobin but not on PATH yet"
                $Script:Installed++
            }
        } else {
            Mark-Failed $Bin
        }
    } catch {
        Mark-Failed $Bin
    }
}

# ---------------------------------------------------------------------------
# Workspace setup
# ---------------------------------------------------------------------------

function Setup-Workspace {
    Write-Header 'Workspace setup'

    if ((Test-Path .env) -or (Test-Path .env.local)) {
        Write-Ok '.env or .env.local already exists — leaving alone'
    } elseif (Test-Path .env.example) {
        Copy-Item .env.example .env
        Write-Ok '.env created from .env.example'
        Write-Warn 'EDIT .env with real production credentials before using any CLI'
    } else {
        Write-Err '.env.example missing — are you running this from the repo root?'
    }

    if (Test-Path infra-knowledge) {
        $seeded = 0
        Get-ChildItem -Path infra-knowledge -Filter '*.example.md' | ForEach-Object {
            $target = $_.FullName -replace '\.example\.md$', '.md'
            if (-not (Test-Path $target)) {
                Copy-Item $_.FullName $target
                $seeded++
            }
        }
        if ($seeded -gt 0) {
            Write-Ok "infra-knowledge: seeded $seeded file(s) from templates"
            Write-Warn 'edit infra-knowledge\*.md with your real service inventory and quirks'
        } else {
            Write-Ok 'infra-knowledge: all template files already seeded'
        }
    }
}

function Show-NextSteps {
    Write-Header 'Next steps'
    @'
  1. Edit .env with your real production credentials:
       notepad .env
  2. Load .env into your current PowerShell session (every new session):
       . .\scripts\activate.ps1
     (Add this to your $PROFILE if you want it to auto-load.)
  3. Verify each connection (one safe read per tool):
       grafana user current -o json
       jenkins status -o json
       cubeapm traces services -o json
       aws sts get-caller-identity --output json
       gh auth status
       psql -c "SHOW default_transaction_read_only;"   # must report 'on'
       mysql -e "SELECT @@transaction_read_only;"      # must report 1
       mongosh "$env:MONGODB_URI" --eval "db.runCommand({ping:1})"
  4. For Kafka tools (kcat, rpk) and direnv-style auto-loading, use WSL2:
       wsl --install
       # then inside WSL: bash scripts/setup.sh
  5. Read CLAUDE.md "Database safety contract" before any DB query.

'@ | Write-Host
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

function Main {
    $repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
    Set-Location $repoRoot

    Write-Host ''
    Write-Host '=== rca-assist setup (Windows) ===' -ForegroundColor White -BackgroundColor DarkGray
    Write-Host "Repo: $repoRoot"

    Test-Preflight

    Write-Header 'Observability & CI/CD'
    Install-Winget -Id 'jqlang.jq'         -Bin 'jq'
    Install-Winget -Id 'Amazon.AWSCLI'     -Bin 'aws'
    Install-Winget -Id 'GitHub.cli'        -Bin 'gh'
    Mark-Skipped 'direnv' 'no maintained Windows port — use WSL2 or manual .env sourcing'

    Write-Header 'Kafka'
    Mark-Skipped 'kcat' 'no Windows binary — use WSL2'
    Mark-Skipped 'rpk'  'no Windows binary — use WSL2'

    Write-Header 'Database clients'
    Install-Winget -Id 'MongoDB.Shell'                 -Bin 'mongosh'
    Install-Winget -Id 'PostgreSQL.PostgreSQL'         -Bin 'psql'  -DisplayName 'psql (PostgreSQL)'
    Install-Winget -Id 'Oracle.MySQL'                  -Bin 'mysql' -DisplayName 'mysql (MySQL Installer)'

    Write-Header 'Kubernetes & cache'
    Install-Winget -Id 'Kubernetes.kubectl'            -Bin 'kubectl'
    Mark-Skipped 'redis-cli' 'no first-party Windows client — use WSL2'

    Write-Header 'Bootstrap (go)'
    Install-Winget -Id 'GoLang.Go' -Bin 'go'

    Write-Header 'Custom CLIs (grafana / jenkins / cubeapm / es)'
    Install-GoCli -Module 'github.com/piyush-gambhir/grafana-cli' -Bin 'grafana'
    Install-GoCli -Module 'github.com/piyush-gambhir/jenkins-cli' -Bin 'jenkins'
    Install-GoCli -Module 'github.com/piyush-gambhir/cubeapm-cli' -Bin 'cubeapm'
    Install-GoCli -Module 'github.com/piyush-gambhir/es-cli'      -Bin 'es'

    Setup-Workspace

    Write-Header 'Summary'
    Write-Host ("  {0} newly installed"   -f $Script:Installed) -ForegroundColor Green
    Write-Host ("  {0} already installed" -f $Script:Already)   -ForegroundColor Blue
    if ($Script:Skipped -gt 0) {
        Write-Host ("  {0} skipped (need WSL2)" -f $Script:Skipped) -ForegroundColor Yellow
    }
    if ($Script:Failed -gt 0) {
        Write-Host ("  {0} failed" -f $Script:Failed) -ForegroundColor Red
    }

    Show-NextSteps

    if ($Script:Failed -gt 0) { exit 1 } else { exit 0 }
}

Main
