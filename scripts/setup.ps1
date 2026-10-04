#Requires -Version 5.1
<#
.SYNOPSIS
    reckon setup for Windows (native PowerShell).

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
    Write-Warn 'After install, select an environment and dot-source scripts/activate.ps1.'
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

function Protect-EnvFile {
    param([Parameter(Mandatory)] [string] $Path)
    if (-not (Test-Path $Path)) { return }
    $acl = Get-Acl $Path
    $acl.SetAccessRuleProtection($true, $false)
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $identity, 'FullControl', 'Allow'
    )
    $acl.SetAccessRule($rule)
    Set-Acl -Path $Path -AclObject $acl
}

# ---------------------------------------------------------------------------
# Workspace setup
# ---------------------------------------------------------------------------

function Setup-Workspace {
    Write-Header 'Workspace setup'

    @(
        '.config\production', '.config\staging', '.config\uat',
        'infra-knowledge\_shared', 'infra-knowledge\production',
        'infra-knowledge\staging', 'infra-knowledge\uat',
        'incidents'
    ) | ForEach-Object {
        New-Item -ItemType Directory -Path $_ -Force | Out-Null
    }
    Write-Ok 'per-environment config, infra-knowledge, and incidents directories ready'

    if (Test-Path .env.production) {
        Write-Ok '.env.production already exists — leaving alone'
    } elseif (Test-Path .env) {
        Copy-Item .env .env.production
        Write-Ok 'migrated legacy .env to .env.production'
        Write-Warn 'the original .env was left in place; remove it after checking .env.production'
    } elseif (Test-Path .env.example) {
        Copy-Item .env.example .env.production
        Write-Ok '.env.production created from .env.example'
        Write-Warn 'EDIT .env.production with real production credentials before using any CLI'
    } else {
        Write-Err '.env.example missing — are you running this from the repo root?'
        $Script:Failed++
    }

    Get-ChildItem -Path . -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^\.env\.(common|production|staging|uat)(\.local)?$' } |
        ForEach-Object { Protect-EnvFile $_.FullName }

    $seeded = 0
    $sharedTemplates = 'skills\reckon\templates\infra-knowledge\_shared'
    $environmentTemplates = 'skills\reckon\templates\infra-knowledge\env'
    if ((Test-Path $sharedTemplates) -and (Test-Path $environmentTemplates)) {
        foreach ($template in (Get-ChildItem -Path $sharedTemplates -Filter '*.md')) {
            $target = Join-Path 'infra-knowledge\_shared' $template.Name
            if (-not (Test-Path $target)) {
                Copy-Item $template.FullName $target
                $seeded++
            }
        }
        foreach ($environment in @('production', 'staging', 'uat')) {
            foreach ($template in (Get-ChildItem -Path $environmentTemplates -Filter '*.md')) {
                $target = Join-Path "infra-knowledge\$environment" $template.Name
                if (-not (Test-Path $target)) {
                    Copy-Item $template.FullName $target
                    $seeded++
                }
            }
        }
        if ($seeded -gt 0) {
            Write-Ok "infra-knowledge: seeded $seeded file(s) from templates"
            Write-Warn 'edit infra-knowledge\<environment>\*.md with real inventory and quirks'
        } else {
            Write-Ok 'infra-knowledge: all template files already seeded'
        }
    } else {
        Write-Err 'infra-knowledge templates are missing'
        $Script:Failed++
    }

    New-Item -ItemType Directory -Path '.agents\skills', '.claude\skills' -Force | Out-Null
    $trackedSkill = Join-Path (Get-Location) 'skills\reckon'
    $agentsSkill = Join-Path (Get-Location) '.agents\skills\reckon'
    $claudeSkill = Join-Path (Get-Location) '.claude\skills\reckon'
    try {
        if (-not (Test-Path $agentsSkill)) {
            New-Item -ItemType Junction -Path $agentsSkill -Target $trackedSkill | Out-Null
        }
        if (-not (Test-Path $claudeSkill)) {
            New-Item -ItemType Junction -Path $claudeSkill -Target $agentsSkill | Out-Null
        }
        if (Test-Path (Join-Path $claudeSkill 'SKILL.md')) {
            Write-Ok 'reckon skill linked (.claude -> .agents -> skills\reckon)'
        } else {
            Write-Err 'reckon skill link did not resolve'
            $Script:Failed++
        }
    } catch {
        Write-Warn "could not create skill junctions: $($_.Exception.Message)"
        Write-Warn 'agents can still read skills\reckon\SKILL.md through AGENTS.md'
    }

    if (Test-Path .reckon-env) {
        $selected = (Get-Content .reckon-env -Raw).Trim()
        if ($selected -in @('production', 'staging', 'uat')) {
            Write-Ok "active environment already selected ($selected)"
        } else {
            Write-Err '.reckon-env is invalid — run: .\scripts\reckon.ps1 use <environment>'
            $Script:Failed++
        }
    } else {
        Write-Warn 'no active environment selected — run: .\scripts\reckon.ps1 use production'
    }
}

function Show-NextSteps {
    Write-Header 'Next steps'
    @'
  1. Select an environment explicitly (reckon never defaults to production):
       .\scripts\reckon.ps1 use production
  2. Edit its credential file:
       notepad .env.production
  3. Load it into your current PowerShell session:
       . .\scripts\activate.ps1
     (Add this to your $PROFILE if you want it to auto-load.)
  4. Inspect and verify the workspace:
       .\scripts\reckon.ps1 doctor
       .\scripts\reckon.ps1 status
       .\scripts\reckon.ps1 verify
  5. For Kafka tools (kcat, rpk) and direnv-style auto-loading, use WSL2:
       wsl --install
       # then inside WSL: bash scripts/setup.sh
  6. Read CLAUDE.md "Database safety contract" before any DB query.

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
    Mark-Skipped 'direnv' 'no maintained Windows port — use activate.ps1'

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

    Write-Header 'Custom CLIs (grafana / jenkins / cubeapm / es)'
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
