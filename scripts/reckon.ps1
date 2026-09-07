#Requires -Version 5.1
<#
.SYNOPSIS
    Native PowerShell control surface for the reckon workspace.

.DESCRIPTION
    Mirrors scripts/reckon for native Windows users: explicit environment
    selection, local readiness inspection, diagnostics, and bounded read-only
    read-only connection probes.
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Command = 'help',

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$ValidEnvironments = @('production', 'staging', 'uat')

if ($Command -in @('debug', 'investigate', 'monitor', 'analyze', 'services', 'capabilities', 'collect', 'resume', 'report', 'note', 'history', 'promote', 'close', 'demo')) {
    if (-not (Get-Command python3 -ErrorAction SilentlyContinue)) { throw 'Python 3.9+ (python3) is required' }
    & python3 (Join-Path $PSScriptRoot 'reckon.py') $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "reckon $Command exited with $LASTEXITCODE" }
    return
}

function Get-IntegrationRegistry {
    Get-Content (Join-Path $PSScriptRoot 'integrations.tsv') | ForEach-Object {
        $parts = $_ -split '\|'
        [pscustomobject]@{ Name=$parts[0]; Binary=$parts[1]; Required=$parts[2]; Config=$parts[3] }
    }
}

function Test-CommandAvailable {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-Placeholder {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $true }
    return $Value -match '(?i)replace_me|example\.com|\.example\.|@example|xxxx|changeme|your-|^<.*>$'
}

function Test-RequiredVariables {
    param([string]$Spec)
    if ($Spec -eq '-') { return $false }

    foreach ($group in ($Spec -split ',')) {
        $hit = $false
        foreach ($variable in ($group -split '/')) {
            $value = [Environment]::GetEnvironmentVariable($variable)
            if ($value -and -not (Test-Placeholder $value)) {
                $hit = $true
                break
            }
        }
        if (-not $hit) { return $false }
    }
    return $true
}

function Test-SavedConfig {
    param([string]$Spec)
    if ($Spec -eq '-' -or -not $env:XDG_CONFIG_HOME) { return $false }

    foreach ($relativePath in ($Spec -split ';')) {
        $path = Join-Path $env:XDG_CONFIG_HOME $relativePath
        if ((Test-Path $path) -and (Get-Item $path).Length -gt 0) { return $true }
    }
    return $false
}

function Test-IntegrationConfigured {
    param($Integration)
    return (Test-RequiredVariables $Integration.Required) -or
        (Test-SavedConfig $Integration.Config)
}

function Initialize-ReckonEnvironment {
    try {
        . (Join-Path $PSScriptRoot 'activate.ps1')
        return $true
    } catch {
        Write-Host "reckon: $($_.Exception.Message)" -ForegroundColor Red
        return $false
    }
}

function Invoke-Verification {
    param(
        [string]$Name,
        [switch]$VerboseOutput
    )

    if (-not (Get-Command python3 -ErrorAction SilentlyContinue)) {
        throw 'Python 3.9+ (python3) is required for bounded verification'
    }
    $output = & python3 (Join-Path $PSScriptRoot 'reckon.py') probe $Name 2>&1
    $succeeded = $LASTEXITCODE -eq 0
    if (-not $succeeded -and $VerboseOutput) {
        $output | Select-Object -First 40 | ForEach-Object { Write-Host "      $_" }
    }
    return $succeeded
}

function Show-Usage {
    @'
reckon — workspace control surface

  .\scripts\reckon.ps1 status
  .\scripts\reckon.ps1 doctor
  .\scripts\reckon.ps1 preflight
  .\scripts\reckon.ps1 verify [--verbose] [integration...]
  .\scripts\reckon.ps1 env
  .\scripts\reckon.ps1 use <production|staging|uat>
  .\scripts\reckon.ps1 debug --help
  .\scripts\reckon.ps1 collect <session> --env <environment>
  .\scripts\reckon.ps1 resume <session> --json
  .\scripts\reckon.ps1 report <session>

verify, collect, and start commands with --collect contact infrastructure.
Saved investigations require Python 3.9+ exposed as python3.
The offline demo currently requires macOS/Linux (or WSL2).
'@ | Write-Host
}

if ($Command -eq 'use') {
    $wanted = $Arguments | Select-Object -First 1
    if ($wanted -notin $ValidEnvironments) {
        throw "usage: .\scripts\reckon.ps1 use <production|staging|uat>"
    }
    Set-Content -Path (Join-Path $RepoRoot '.reckon-env') -Value $wanted -Encoding ASCII
    Write-Host "  ✓ active environment -> $wanted" -ForegroundColor Green
    Write-Host '  dot-source .\scripts\activate.ps1 to apply it to this shell'
    return
}

if ($Command -in @('help', '-h', '--help', '')) {
    Show-Usage
    return
}

if ($Command -eq 'env') {
    Write-Output ". `"$PSScriptRoot\activate.ps1`""
    return
}

if ($Command -notin @('status', 'doctor', 'preflight', 'verify')) {
    Show-Usage
    throw "unknown command: $Command"
}

if (-not (Initialize-ReckonEnvironment)) { throw 'failed to initialize reckon environment' }
$registry = @(Get-IntegrationRegistry)

switch ($Command) {
    'status' {
        Write-Host "`nreckon — environment" -ForegroundColor White
        Write-Host "  ENV=$($env:RECKON_ENV)"
        Write-Host "  config $($env:XDG_CONFIG_HOME)"
        Write-Host "`nintegrations"
        $ready = 0
        foreach ($integration in $registry) {
            $hasCli = Test-CommandAvailable $integration.Binary
            $configured = Test-IntegrationConfigured $integration
            if ($hasCli -and $configured) { $ready++ }
            '{0,-16} CLI={1,-3} credentials={2}' -f
                $integration.Name,
                $(if ($hasCli) { 'yes' } else { 'no' }),
                $(if ($configured) { 'configured' } else { '-' }) | Write-Host
        }
        Write-Host "`n  $ready of $($registry.Count) integrations locally configured"
        Write-Host '  run reckon.ps1 verify to prove live connectivity'
    }
    'doctor' {
        Write-Host "`nreckon doctor — $($env:RECKON_ENV)"
        $problems = 0
        $envFile = Join-Path $RepoRoot ".env.$($env:RECKON_ENV)"
        if (Test-Path $envFile) { Write-Host "  ✓ $([IO.Path]::GetFileName($envFile)) present" -ForegroundColor Green }
        else { Write-Warning "missing $envFile" }
        if (Test-Path $env:XDG_CONFIG_HOME) { Write-Host '  ✓ environment config directory exists' -ForegroundColor Green }
        else { Write-Warning 'environment config directory is missing' }
        $skillFile = Join-Path $RepoRoot '.claude\skills\reckon\SKILL.md'
        if (Test-Path $skillFile) { Write-Host '  ✓ skill link resolves' -ForegroundColor Green }
        else { Write-Warning 'skill link is missing'; $problems++ }
        foreach ($knowledgePath in @('infra-knowledge\_shared', "infra-knowledge\$($env:RECKON_ENV)")) {
            $fullPath = Join-Path $RepoRoot $knowledgePath
            $count = @(Get-ChildItem $fullPath -Filter '*.md' -ErrorAction SilentlyContinue).Count
            if ($count -gt 0) { Write-Host "  ✓ $knowledgePath — $count file(s)" -ForegroundColor Green }
            else { Write-Warning "$knowledgePath is empty" }
        }
        if ($problems -gt 0) { throw "$problems workspace problem(s) need fixing" }
        Write-Host "`n  workspace healthy" -ForegroundColor Green
    }
    'preflight' {
        $configured = @()
        $unavailable = @()
        foreach ($integration in $registry) {
            if (-not (Test-CommandAvailable $integration.Binary)) {
                $unavailable += "$($integration.Name)(no cli)"
            } elseif (-not (Test-IntegrationConfigured $integration)) {
                $unavailable += "$($integration.Name)(no creds)"
            } else {
                $configured += $integration.Name
            }
        }
        Write-Host "reckon preflight — ENV=$($env:RECKON_ENV)"
        Write-Host "configured:  $(if ($configured.Count) { $configured -join ' ' } else { '(none)' })"
        Write-Host "unavailable: $(if ($unavailable.Count) { $unavailable -join ' ' } else { '(none)' })"
        Write-Host 'note:        configured is local state; run reckon.ps1 verify for live connectivity'
    }
    'verify' {
        $verboseOutput = $Arguments -contains '--verbose' -or $Arguments -contains '-v'
        $targets = @($Arguments | Where-Object { $_ -notin @('--verbose', '-v') })
        $known = @($registry.Name)
        foreach ($target in $targets) {
            if ($target.StartsWith('-') -or $target -notin $known) {
                throw "unknown integration or option: $target"
            }
        }
        Write-Host "`nreckon verify — $($env:RECKON_ENV)"
        if ($env:RECKON_ENV -eq 'production') {
            Write-Warning 'these are live reads against PRODUCTION'
        }
        $ok = 0
        $failed = 0
        $skipped = 0
        foreach ($integration in $registry) {
            if ($targets.Count -gt 0 -and $integration.Name -notin $targets) { continue }
            if (-not (Test-CommandAvailable $integration.Binary) -or
                -not (Test-IntegrationConfigured $integration)) {
                Write-Host "  — $($integration.Name) skipped (not locally ready)"
                $skipped++
                continue
            }
            if (Invoke-Verification $integration.Name -VerboseOutput:$verboseOutput) {
                Write-Host "  ✓ $($integration.Name)" -ForegroundColor Green
                $ok++
            } else {
                Write-Host "  ✗ $($integration.Name) — probe failed" -ForegroundColor Red
                if (-not $verboseOutput) {
                    Write-Host "    retry with: .\scripts\reckon.ps1 verify --verbose $($integration.Name)"
                }
                $failed++
            }
        }
        Write-Host "`n  $ok ok, $failed failed, $skipped skipped"
        if ($failed -gt 0 -or ($targets.Count -gt 0 -and $skipped -gt 0)) {
            throw 'one or more requested verification probes did not succeed'
        }
    }
}
