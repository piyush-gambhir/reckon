#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
foreach ($relative in @('target/release/reckon.exe', 'target/release/reckon', 'target/debug/reckon.exe', 'target/debug/reckon')) {
    $binary = Join-Path $repoRoot $relative
    if (Test-Path -LiteralPath $binary -PathType Leaf) {
        & $binary @args
        exit $LASTEXITCODE
    }
}
throw 'Build the native CLI first: cargo build --locked --release'
