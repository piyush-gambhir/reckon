#Requires -Version 5.1
<#
.SYNOPSIS
    Windows direnv-replacement for rca-assist.

.DESCRIPTION
    PowerShell does not have direnv. This script does what .envrc does on
    macOS / Linux: load .env (and .env.local), set XDG_CONFIG_HOME and the
    AWS path overrides, apply the DB read-only safety defaults, and apply
    the same compatibility aliases.

    Dot-source it (note the leading dot + space) to apply to your CURRENT
    PowerShell session — running it normally would only set env vars in a
    child process and they'd vanish when it exits.

.EXAMPLE
    PS> . .\scripts\activate.ps1
    PS> aws sts get-caller-identity --output json

.NOTES
    To auto-activate every time you open PowerShell in this repo, add to
    your $PROFILE something like:
        if ($PWD.Path -like '*\rca-assist*') { . .\scripts\activate.ps1 }
#>

$ErrorActionPreference = 'Stop'

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')

# ---------------------------------------------------------------------------
# 1. XDG_CONFIG_HOME = $repoRoot/.config (mirrors .envrc behaviour)
# ---------------------------------------------------------------------------
$xdg = Join-Path $repoRoot '.config'
if (-not (Test-Path $xdg)) { New-Item -ItemType Directory -Path $xdg -Force | Out-Null }
$env:XDG_CONFIG_HOME = $xdg

# ---------------------------------------------------------------------------
# 2. Load .env then .env.local (later overrides earlier)
# ---------------------------------------------------------------------------
function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return }

    # NOTE: values are taken literally — unlike bash `. .env`, this performs no
    # variable expansion or command substitution. Keep .env values self-contained.
    Get-Content $Path -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith('#')) { return }
        # Match KEY=VALUE, allowing optional 'export' prefix and quoted values.
        if ($line -match '^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $key = $Matches[1]
            $val = $Matches[2].Trim()
            # Strip surrounding single or double quotes; otherwise drop a
            # whitespace-preceded trailing comment, matching bash source semantics.
            if ($val -match '^"(.*)"$' -or $val -match "^'(.*)'$") {
                $val = $Matches[1]
            } else {
                $val = ($val -replace '\s+#.*$', '').Trim()
            }
            Set-Item -Path "Env:$key" -Value $val
        }
    }
}

Import-DotEnv (Join-Path $repoRoot '.env')
Import-DotEnv (Join-Path $repoRoot '.env.local')

# ---------------------------------------------------------------------------
# 3. AWS path overrides (aws CLI ignores XDG_CONFIG_HOME natively)
# ---------------------------------------------------------------------------
$env:AWS_CONFIG_FILE             = Join-Path $xdg 'aws\config'
$env:AWS_SHARED_CREDENTIALS_FILE = Join-Path $xdg 'aws\credentials'

# ---------------------------------------------------------------------------
# 4. GitHub token alias
# ---------------------------------------------------------------------------
if ($env:GITHUB_TOKEN -and -not $env:GH_TOKEN) {
    $env:GH_TOKEN = $env:GITHUB_TOKEN
}

# ---------------------------------------------------------------------------
# 5. Database safety defaults (only applied if .env didn't already set them)
# ---------------------------------------------------------------------------
# PostgreSQL: psql honours PGOPTIONS as the session default (the read-only role
# remains the real write guard).
if (-not $env:PGOPTIONS) {
    $env:PGOPTIONS = '-c statement_timeout=30s -c default_transaction_read_only=on'
}
# MySQL: the client has no init-command env var, so write a workspace option
# file and invoke `mysql --defaults-extra-file="$env:XDG_CONFIG_HOME\mysql\my.cnf"`.
if ($env:MYSQL_HOST) {
    $myDir = Join-Path $xdg 'mysql'
    if (-not (Test-Path $myDir)) { New-Item -ItemType Directory -Path $myDir -Force | Out-Null }
    $myLines = @('[client]', 'init-command=SET SESSION TRANSACTION READ ONLY')
    if ($env:MYSQL_USER)     { $myLines += "user=$($env:MYSQL_USER)" }
    if ($env:MYSQL_DATABASE) { $myLines += "database=$($env:MYSQL_DATABASE)" }
    Set-Content -Path (Join-Path $myDir 'my.cnf') -Value $myLines -Encoding ASCII
}
# Kafka: rpk reads RPK_* (not KAFKA_*); derive them so rpk can authenticate.
if ($env:KAFKA_BOOTSTRAP_SERVERS) {
    if (-not $env:RPK_BROKERS) { $env:RPK_BROKERS = $env:KAFKA_BOOTSTRAP_SERVERS }
    if ($env:KAFKA_SECURITY_PROTOCOL -in @('SASL_SSL', 'SASL_PLAINTEXT')) {
        if (-not $env:RPK_USER -and $env:KAFKA_SASL_USERNAME)           { $env:RPK_USER = $env:KAFKA_SASL_USERNAME }
        if (-not $env:RPK_PASS -and $env:KAFKA_SASL_PASSWORD)           { $env:RPK_PASS = $env:KAFKA_SASL_PASSWORD }
        if (-not $env:RPK_SASL_MECHANISM -and $env:KAFKA_SASL_MECHANISM) { $env:RPK_SASL_MECHANISM = $env:KAFKA_SASL_MECHANISM }
    }
    if ($env:KAFKA_SECURITY_PROTOCOL -in @('SASL_SSL', 'SSL')) {
        if (-not $env:RPK_TLS_ENABLED) { $env:RPK_TLS_ENABLED = 'true' }
    }
}

# ---------------------------------------------------------------------------
# 6. Compatibility aliases (mirrors .envrc)
# ---------------------------------------------------------------------------
if ($env:JENKINS_USERNAME -and -not $env:JENKINS_USER) {
    $env:JENKINS_USER = $env:JENKINS_USERNAME
}
if ($env:CUBEAPM_HOST -and -not $env:CUBEAPM_SERVER) {
    $env:CUBEAPM_SERVER = $env:CUBEAPM_HOST
}

Write-Host "rca-assist environment loaded from $repoRoot" -ForegroundColor Green
Write-Host ("  XDG_CONFIG_HOME = {0}" -f $env:XDG_CONFIG_HOME)
Write-Host ("  AWS_CONFIG_FILE = {0}" -f $env:AWS_CONFIG_FILE)
Write-Host ("  PGOPTIONS       = {0}" -f $env:PGOPTIONS)
if ($env:MYSQL_HOST) {
    Write-Host ("  MYSQL my.cnf    = {0}" -f (Join-Path $xdg 'mysql\my.cnf'))
}
