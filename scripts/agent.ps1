#Requires -Version 5.1
<#
.SYNOPSIS
    Launch an agent runtime with the selected reckon environment active.
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('claude', 'codex', 'opencode')]
    [string]$Runtime,

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$RuntimeArguments
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'activate.ps1')

if (-not $Runtime) {
    $Runtime = @('claude', 'codex', 'opencode') |
        Where-Object { Get-Command $_ -ErrorAction SilentlyContinue } |
        Select-Object -First 1
}

if (-not $Runtime) {
    Write-Error 'No agent runtime found on PATH. Install claude, codex, or opencode.'
    exit 1
}

if (-not (Get-Command $Runtime -ErrorAction SilentlyContinue)) {
    Write-Error "$Runtime is not on PATH"
    exit 1
}

& $Runtime @RuntimeArguments
exit $LASTEXITCODE
