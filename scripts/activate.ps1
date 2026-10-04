#Requires -Version 5.1
<#
.SYNOPSIS
    Windows direnv-replacement for reckon.

.DESCRIPTION
    PowerShell does not have direnv. This script does what .envrc does on
    macOS / Linux: resolve RECKON_ENV, load its environment files, set
    per-environment CLI config paths, apply the DB read-only safety defaults,
    and apply the same compatibility aliases.

    Dot-source it (note the leading dot + space) to apply to your CURRENT
    PowerShell session — running it normally would only set env vars in a
    child process and they'd vanish when it exits.

.EXAMPLE
    PS> . .\scripts\activate.ps1
    PS> aws sts get-caller-identity --output json

.EXAMPLE
    PS> . .\scripts\activate.ps1 -Env staging

.NOTES
    To auto-activate every time you open PowerShell in this repo, add to
    your $PROFILE something like:
        if ($PWD.Path -like '*\reckon*') { . .\scripts\activate.ps1 }
#>

[CmdletBinding()]
param(
    [string]$Env
)

$ErrorActionPreference = 'Stop'

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')

# A RECKON_ENV that differs from the value the previous activation set was
# changed in this shell (set or removed). It stays the shell's own setting:
# the restoration below must not put back the value from before activation.
$previousEnv = Get-Variable -Name ReckonActivatedEnv -Scope Script -ValueOnly -ErrorAction SilentlyContinue
$shellChangedEnv = $previousEnv -and $env:RECKON_ENV -cne $previousEnv
$shellEnv = $env:RECKON_ENV
$script:ReckonActivatedEnv = $null

# Dot-sourcing a second environment in the same PowerShell session must not
# retain credentials that existed only in the first one. Restore every env var
# managed by the previous activation before resolving and loading the next one.
if (Get-Variable -Name ReckonManagedEnvironment -Scope Script -ErrorAction SilentlyContinue) {
    foreach ($key in @($script:ReckonManagedEnvironment.Keys)) {
        $original = $script:ReckonManagedEnvironment[$key]
        if ($original.Exists) {
            Set-Item -Path "Env:$key" -Value $original.Value
        } else {
            Remove-Item -Path "Env:$key" -ErrorAction SilentlyContinue
        }
    }
}
if ($shellChangedEnv) {
    if ($shellEnv) { $env:RECKON_ENV = $shellEnv } else { Remove-Item -Path Env:RECKON_ENV -ErrorAction SilentlyContinue }
}
$script:ReckonManagedEnvironment = @{}

# Authentication is environment-local, including on the first activation from
# a shell that already has provider credentials. Clear inherited namespaces.
Get-ChildItem Env: | Where-Object {
    $_.Name -match '^(GRAFANA_|JENKINS_|CUBEAPM_|GH_|GITHUB_|AWS_|KAFKA_|RPK_|REDIS_|MONGODB_|PG|MYSQL_|CLICKHOUSE_|ES_|JIRA_|NGINXPM_|KUBECONFIG$)'
} | ForEach-Object { Remove-Item -Path "Env:$($_.Name)" }

# ---------------------------------------------------------------------------
# 1. Resolve the environment before loading any credentials
# ---------------------------------------------------------------------------
$requestedEnv = $Env
if ([string]::IsNullOrEmpty($requestedEnv)) {
    $requestedEnv = $env:RECKON_ENV
}
if ([string]::IsNullOrEmpty($requestedEnv)) {
    $selectionFile = Join-Path $repoRoot '.reckon-env'
    if (Test-Path $selectionFile) {
        $requestedEnv = (Get-Content $selectionFile -Raw).Trim()
    }
}

function Set-ReckonEnvValue {
    param(
        [string]$Name,
        [string]$Value
    )
    if (-not $script:ReckonManagedEnvironment.ContainsKey($Name)) {
        $current = Get-Item -Path "Env:$Name" -ErrorAction SilentlyContinue
        $script:ReckonManagedEnvironment[$Name] = @{
            Exists = ($null -ne $current)
            Value  = $(if ($null -ne $current) { $current.Value } else { $null })
        }
    }
    Set-Item -Path "Env:$Name" -Value $Value
}

if ([string]::IsNullOrEmpty($requestedEnv)) {
    Write-Error 'reckon: no environment selected. Run .\scripts\reckon.ps1 use <production|staging|uat> first.'
    return
}
if ($requestedEnv -cnotin @('production', 'staging', 'uat')) {
    Write-Error "reckon: invalid RECKON_ENV='$requestedEnv'. Valid values: production, staging, uat"
    return
}

# An explicit -Env is this shell's own choice, like setting $env:RECKON_ENV, so
# later activations without -Env keep it instead of restoring an older value.
if ($Env) { $env:RECKON_ENV = $Env }
Set-ReckonEnvValue -Name 'RECKON_ENV' -Value $requestedEnv
if ($requestedEnv -ceq 'production') {
    Write-Host '!!! reckon: ENV=production (PRODUCTION) !!!' -ForegroundColor Red -BackgroundColor Yellow
} else {
    Write-Host ("=== reckon: ENV={0} ===" -f $requestedEnv) -ForegroundColor Cyan
}

# ---------------------------------------------------------------------------
# 2. Per-environment XDG_CONFIG_HOME (mirrors .envrc behaviour)
# ---------------------------------------------------------------------------
$xdg = Join-Path $repoRoot ".config\$requestedEnv"
if (-not (Test-Path $xdg)) { New-Item -ItemType Directory -Path $xdg -Force | Out-Null }
Set-ReckonEnvValue -Name 'XDG_CONFIG_HOME' -Value $xdg

# ---------------------------------------------------------------------------
# 3. Load common, environment, then local credentials (later overrides earlier)
# ---------------------------------------------------------------------------
function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return }

    # NOTE: values are taken literally — unlike bash sourcing, this performs no
    # variable expansion or command substitution. Keep env values self-contained.
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
            Set-ReckonEnvValue -Name $key -Value $val
        }
    }
}

Import-DotEnv (Join-Path $repoRoot '.env.common')
Import-DotEnv (Join-Path $repoRoot ".env.$requestedEnv")
Import-DotEnv (Join-Path $repoRoot ".env.$requestedEnv.local")
# Environment files cannot redirect the selected environment or its CLI state.
Set-ReckonEnvValue -Name 'RECKON_ENV' -Value $requestedEnv
Set-ReckonEnvValue -Name 'XDG_CONFIG_HOME' -Value $xdg
$script:ReckonActivatedEnv = $requestedEnv

# ---------------------------------------------------------------------------
# 4. CLI path overrides
# ---------------------------------------------------------------------------
Set-ReckonEnvValue -Name 'AWS_CONFIG_FILE' -Value (Join-Path $xdg 'aws\config')
Set-ReckonEnvValue -Name 'AWS_SHARED_CREDENTIALS_FILE' -Value (Join-Path $xdg 'aws\credentials')
Set-ReckonEnvValue -Name 'KUBECONFIG' -Value (Join-Path $xdg 'kube\config')

# ---------------------------------------------------------------------------
# 5. GitHub token alias
# ---------------------------------------------------------------------------
if ($env:GITHUB_TOKEN -and -not $env:GH_TOKEN) {
    Set-ReckonEnvValue -Name 'GH_TOKEN' -Value $env:GITHUB_TOKEN
}

# ---------------------------------------------------------------------------
# 6. Database safety defaults (only applied if env files didn't already set them)
# ---------------------------------------------------------------------------
foreach ($prefix in @('GRAFANA', 'JENKINS', 'ES', 'JIRA', 'NGINXPM')) {
    Set-ReckonEnvValue -Name "${prefix}_READ_ONLY" -Value 'true'
}
# PostgreSQL: psql honours PGOPTIONS as the session default (the read-only role
# remains the real write guard).
if (-not $env:PGOPTIONS) {
    Set-ReckonEnvValue -Name 'PGOPTIONS' -Value '-c statement_timeout=30s -c default_transaction_read_only=on'
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
# ClickHouse: only apply defaults when the connection is configured.
# Every client invocation must still pass --readonly=1 explicitly; the
# server-side readonly=1 user profile remains the real write barrier.
if ($env:CLICKHOUSE_HOST) {
    if (-not $env:CLICKHOUSE_PORT)   { Set-ReckonEnvValue -Name 'CLICKHOUSE_PORT' -Value '9440' }
    if (-not $env:CLICKHOUSE_SECURE) { Set-ReckonEnvValue -Name 'CLICKHOUSE_SECURE' -Value '1' }
}
# Kafka: rpk reads RPK_* (not KAFKA_*); derive them so rpk can authenticate.
if ($env:KAFKA_BOOTSTRAP_SERVERS) {
    if (-not $env:RPK_BROKERS) { Set-ReckonEnvValue -Name 'RPK_BROKERS' -Value $env:KAFKA_BOOTSTRAP_SERVERS }
    if ($env:KAFKA_SECURITY_PROTOCOL -in @('SASL_SSL', 'SASL_PLAINTEXT')) {
        if (-not $env:RPK_USER -and $env:KAFKA_SASL_USERNAME) {
            Set-ReckonEnvValue -Name 'RPK_USER' -Value $env:KAFKA_SASL_USERNAME
        }
        if (-not $env:RPK_PASS -and $env:KAFKA_SASL_PASSWORD) {
            Set-ReckonEnvValue -Name 'RPK_PASS' -Value $env:KAFKA_SASL_PASSWORD
        }
        if (-not $env:RPK_SASL_MECHANISM -and $env:KAFKA_SASL_MECHANISM) {
            Set-ReckonEnvValue -Name 'RPK_SASL_MECHANISM' -Value $env:KAFKA_SASL_MECHANISM
        }
    }
    if ($env:KAFKA_SECURITY_PROTOCOL -in @('SASL_SSL', 'SSL')) {
        if (-not $env:RPK_TLS_ENABLED) { Set-ReckonEnvValue -Name 'RPK_TLS_ENABLED' -Value 'true' }
    }
}

# ---------------------------------------------------------------------------
# 7. Compatibility aliases and safety exports (mirrors .envrc)
# ---------------------------------------------------------------------------
if ($env:JENKINS_USERNAME -and -not $env:JENKINS_USER) {
    Set-ReckonEnvValue -Name 'JENKINS_USER' -Value $env:JENKINS_USERNAME
}
if ($env:CUBEAPM_HOST -and -not $env:CUBEAPM_SERVER) {
    Set-ReckonEnvValue -Name 'CUBEAPM_SERVER' -Value $env:CUBEAPM_HOST
}
if (-not $env:ES_READ_ONLY) {
    Set-ReckonEnvValue -Name 'ES_READ_ONLY' -Value 'true'
}

Write-Host "reckon $requestedEnv environment loaded from $repoRoot" -ForegroundColor Green
Write-Host ("  XDG_CONFIG_HOME = {0}" -f $env:XDG_CONFIG_HOME)
Write-Host ("  AWS_CONFIG_FILE = {0}" -f $env:AWS_CONFIG_FILE)
Write-Host ("  PGOPTIONS       = {0}" -f $env:PGOPTIONS)
if ($env:MYSQL_HOST) {
    Write-Host ("  MYSQL my.cnf    = {0}" -f (Join-Path $xdg 'mysql\my.cnf'))
}
if ($env:CLICKHOUSE_HOST) {
    Write-Host ("  CLICKHOUSE      = {0}:{1} (secure={2})" -f $env:CLICKHOUSE_HOST, $env:CLICKHOUSE_PORT, $env:CLICKHOUSE_SECURE)
}
