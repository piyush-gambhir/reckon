#Requires -Version 5.1
<#
.SYNOPSIS
    reckon for native PowerShell. All logic lives in scripts/reckon.py.

.DESCRIPTION
    Native Windows is experimental. WSL2 with scripts/reckon is the supported path.
#>
$ErrorActionPreference = 'Stop'
$python = @('python3', 'python') | Where-Object { Get-Command $_ -ErrorAction SilentlyContinue } | Select-Object -First 1
if (-not $python) { throw 'Python 3.9+ (python3) is required' }
& $python (Join-Path $PSScriptRoot 'reckon.py') @args
exit $LASTEXITCODE
