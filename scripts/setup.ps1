#Requires -Version 5.1
<#
.SYNOPSIS
    reckon setup for Windows (native PowerShell).

.DESCRIPTION
    Installs every CLI that has a working native Windows port via winget,
    plus the custom Go-based CLIs. Tools without good native Windows support
    (kcat, rpk) are skipped with a clear message. For the full
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
    Write-Warn '  - kcat    (no Windows binary)'
    Write-Warn '  - rpk     (no Windows binary)'
    Write-Warn 'For the full experience, use WSL2 + scripts/setup.sh.'
    Write-Warn 'After install, create an org: python3 .\scripts\reckon.py org add <org>'
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
        [Parameter(Mandatory)] [string] $Bin,
        [Parameter(Mandatory)] [string] $Version,
        [Parameter(Mandatory)] [string] $Release,
        [Parameter(Mandatory)] [string] $VersionPackage
    )
    if (Test-Command $Bin) { Mark-Already $Bin; return }
    if (-not (Test-Command go)) { Mark-Failed "$Bin (go not installed)"; return }
    $previousGoBin = $env:GOBIN
    $previousEAP = $ErrorActionPreference
    $stage = Join-Path ([IO.Path]::GetTempPath()) ([IO.Path]::GetRandomFileName())
    Write-Info "$Bin — installing $Release ($Module@$Version)..."
    try {
        $destination = (& go env GOBIN).Trim()
        if (-not $destination) { $destination = Join-Path ((& go env GOPATH).Trim()) 'bin' }
        New-Item -ItemType Directory -Path $stage -Force | Out-Null
        $env:GOBIN = $stage
        # PowerShell 5.1 treats native stderr as ErrorRecord; Go writes normal
        # download progress there. Use its exit code, restoring state in finally.
        $ErrorActionPreference = 'Continue'
        & go install -ldflags "-X $Module/$VersionPackage.Version=$($Release.TrimStart('v'))" "$Module@$Version" 2>&1 | Out-Null
        $installExitCode = $LASTEXITCODE
        $ErrorActionPreference = $previousEAP
        if ($installExitCode -ne 0) { Mark-Failed $Bin; return }
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
        Move-Item -LiteralPath (Join-Path $stage 'cli-go.exe') -Destination (Join-Path $destination "$Bin.exe") -Force
        $env:Path = "$destination;$env:Path"
        Mark-Installed $Bin
    } catch {
        Mark-Failed $Bin
    } finally {
        $env:GOBIN = $previousGoBin
        $ErrorActionPreference = $previousEAP
        if (Test-Path $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
    }
}

# ---------------------------------------------------------------------------
# Reckon home
# ---------------------------------------------------------------------------

function Setup-Workspace {
    Write-Header 'Reckon home'
    $home_ = if ($env:RECKON_HOME) { $env:RECKON_HOME } else { Join-Path $HOME '.reckon' }
    # Business data lives here, never in this checkout. reckon org add and
    # reckon connect create orgs, connections and their credential files.
    New-Item -ItemType Directory -Path $home_ -Force | Out-Null
    Write-Ok "Reckon home: $home_"
    Write-Warn 'Native Windows is experimental: reckon open needs symlink rights (Developer Mode).'
}

function Show-NextSteps {
    Write-Header 'Next steps'
    @'
  1. Create an org for each business:
       python3 .\scripts\reckon.py org add acme --envs 'prod,uat,dev'
  2. Connect its tools (credentials go in the file it prints, never in chat):
       python3 .\scripts\reckon.py connect jenkins-prod --org acme --provider jenkins --for-env prod
  3. Check them, then start the agent pinned to one environment:
       python3 .\scripts\reckon.py use acme/prod
       python3 .\scripts\reckon.py verify
       python3 .\scripts\reckon.py open
  4. For Kafka tools (kcat, rpk), use WSL2:
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
    Write-Host '=== reckon setup (Windows) ===' -ForegroundColor White -BackgroundColor DarkGray
    Write-Host "Repo: $repoRoot"

    Test-Preflight

    if (Get-Command python3 -ErrorAction SilentlyContinue) {
        & python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))'
        if ($LASTEXITCODE -ne 0) {
            Write-Err 'Python 3.9+ is required for investigations; upgrade python3 on PATH'
            $Script:Failed++
        }
    } else {
        Write-Warn 'Install Python 3.9+ and expose it as python3 for investigations and bounded verification'
        $Script:Failed++
    }

    Write-Header 'Observability & CI/CD'
    Install-Winget -Id 'jqlang.jq'         -Bin 'jq'
    Install-Winget -Id 'Amazon.AWSCLI'     -Bin 'aws'
    Install-Winget -Id 'GitHub.cli'        -Bin 'gh'

    Write-Header 'Kafka'
    Mark-Skipped 'kcat' 'no Windows binary — use WSL2'
    Mark-Skipped 'rpk'  'no Windows binary — use WSL2'

    Write-Header 'Database clients'
    Install-Winget -Id 'MongoDB.Shell'                 -Bin 'mongosh'
    Install-Winget -Id 'PostgreSQL.PostgreSQL'         -Bin 'psql'  -DisplayName 'psql (PostgreSQL)'
    Install-Winget -Id 'Oracle.MySQL'                  -Bin 'mysql' -DisplayName 'mysql (MySQL Installer)'
    Mark-Skipped 'clickhouse' 'install manually or use WSL2'

    Write-Header 'Kubernetes & cache'
    Install-Winget -Id 'Kubernetes.kubectl'            -Bin 'kubectl'
    Mark-Skipped 'redis-cli' 'no first-party Windows client — use WSL2'

    Write-Header 'Bootstrap (go)'
    Install-Winget -Id 'GoLang.Go' -Bin 'go'

    Write-Header 'Custom CLIs (pinned in cli-releases.csv)'
    Import-Csv (Join-Path $PSScriptRoot 'cli-releases.csv') | ForEach-Object {
        Install-GoCli -Module $_.module -Bin $_.binary -Version $_.version -Release $_.release -VersionPackage $_.version_package
    }

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
