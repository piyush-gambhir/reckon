#!/usr/bin/env bash
# Exercise the installer without downloading tools or changing the user's PATH.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../scripts/lib/go-clis.sh
source "$ROOT/scripts/lib/go-clis.sh"
FIXTURE="$(mktemp -d)"
trap 'rm -rf -- "$FIXTURE"' EXIT
HOME="$FIXTURE/home"
GOBIN_DIR="$FIXTURE/bin with spaces"
GOBIN="preserve-caller-gobin"
PLATFORM=macos
ARCH=arm64
PATH="$HOME/.local/bin:$FIXTURE/old:$GOBIN_DIR:/usr/bin:/bin"
INSTALLED=0
FAILED=0
ALREADY=0
have() { command -v "$1" >/dev/null 2>&1; }
info() { :; }
warn() { :; }
mark_already() { ALREADY=$((ALREADY + 1)); }
mark_installed() { INSTALLED=$((INSTALLED + 1)); }
mark_failed() { FAILED=$((FAILED + 1)); }
# A fake CLI that reports version $2 the way gsc does: the latest release it
# knows of comes first.
fake() {
    mkdir -p "$(dirname "$1")"
    printf '#!/bin/sh\nprintf "latest  0.0.1\\nversion %s\\n"\n' "$2" > "$1"
    chmod +x "$1"
}
download_install_bin() {
    printf '%s\n' "$@" > "$FIXTURE/download"
    [ "${FAIL_DOWNLOAD:-0}" -eq 0 ] || return 1
    fake "$6/$4" 99.0.0
}
go() {
    printf '%s\n' "$@" > "$FIXTURE/args"
    printf '%s\n' "$GOBIN" > "$FIXTURE/stage"
    [ "${FAIL_BUILD:-0}" -eq 0 ] || return 1
    touch "$GOBIN/cli-go"
    chmod +x "$GOBIN/cli-go"
}

refute() { if "$@"; then echo "unexpectedly true: $*"; exit 1; fi; }
fake "$FIXTURE/v/a" 0.1.8
[ "$(cli_version "$FIXTURE/v/a")" = 0.1.8 ]
version_older 0.1.6 0.1.8
version_older 0.9.9 0.10.0
refute version_older 0.2.10 0.2.10
refute version_older 1.0.0 0.9.9

# Every pinned CLI installs from its own release archive, checked against the release's checksums.
while IFS=, read -r binary module version release version_package; do
    [ "$binary" = binary ] && continue
    [[ "$module" == github.com/*/cli-go ]]
    repo="${module#github.com/}"
    repo="${repo%/cli-go}"
    install_go_cli "$module" "$binary" "$version" "$release" "$version_package"
    [ -x "$HOME/.local/bin/$binary" ]
    url="https://github.com/$repo/releases/download/$release"
    [ "$(cat "$FIXTURE/download")" = "$(printf '%s\n' "$url/${repo##*/}_darwin_arm64.tar.gz" tar.gz "$binary" "$binary" "$url/checksums.txt" "$HOME/.local/bin")" ]
done < "$ROOT/scripts/cli-releases.csv"
[ "$INSTALLED" -eq "$(($(wc -l < "$ROOT/scripts/cli-releases.csv") - 1))" ]
[ ! -e "$FIXTURE/args" ]

# Current or unknown versions are left alone; an older copy is replaced where it is.
install_go_cli github.com/acme/grafana-cli/cli-go grafana ignored v0.2.10 internal/build
[ "$ALREADY" -eq 1 ]
mkdir -p "$FIXTURE/old"
printf '#!/bin/sh\necho dev build\n' > "$FIXTURE/old/devcli"
chmod +x "$FIXTURE/old/devcli"
install_go_cli github.com/acme/devcli/cli-go devcli ignored v1.0.0 internal/build
[ "$ALREADY" -eq 2 ]
fake "$FIXTURE/old/stale" 0.1.6
INSTALLED=0
install_go_cli github.com/acme/stale-cli/cli-go stale ignored v0.1.8 internal/build
[ "$INSTALLED" -eq 1 ]
[ "$(tail -n 1 "$FIXTURE/download")" = "$FIXTURE/old" ]
[ "$(cli_version stale)" = 99.0.0 ]

# An older copy setup cannot replace, earlier on PATH, makes the upgrade fail rather than pass.
mkdir -p "$FIXTURE/locked"
fake "$FIXTURE/locked/locked" 0.1.6
chmod 555 "$FIXTURE/locked"
if [ ! -w "$FIXTURE/locked" ]; then
    PATH="$FIXTURE/locked:$PATH" install_go_cli github.com/acme/locked-cli/cli-go locked ignored v0.1.8 internal/build
    [ "$FAILED" -eq 1 ]
    [ "$(tail -n 1 "$FIXTURE/download")" = "$HOME/.local/bin" ]
    FAILED=0
fi
chmod 755 "$FIXTURE/locked"

# No archive: build from source with the pinned version, staged so the binaries cannot clash.
FAIL_DOWNLOAD=1
install_go_cli github.com/acme/built-cli/cli-go built v1.2.3 v1.2.3 internal/build
[ "$INSTALLED" -eq 2 ]
[ -x "$GOBIN_DIR/built" ]
[ ! -e "$GOBIN_DIR/cli-go" ]
grep -Fxq -- "github.com/acme/built-cli/cli-go@v1.2.3" "$FIXTURE/args"
grep -Fxq -- "-X github.com/acme/built-cli/cli-go/internal/build.Version=1.2.3" "$FIXTURE/args"
[ ! -e "$(cat "$FIXTURE/stage")" ]
[ "$GOBIN" = preserve-caller-gobin ]

# Upgrading from source replaces the old copy where it is, too.
fake "$FIXTURE/old/stale2" 0.1.6
install_go_cli github.com/acme/stale2-cli/cli-go stale2 v0.1.8 v0.1.8 internal/build
[ "$INSTALLED" -eq 3 ]
[ ! -s "$FIXTURE/old/stale2" ]
[ ! -e "$GOBIN_DIR/stale2" ]

# Both fail: nothing is left behind.
FAIL_BUILD=1
install_go_cli github.com/acme/missing-cli/cli-go missing v1.0.0 v1.0.0 internal/build
[ "$FAILED" -eq 1 ]
[ ! -e "$GOBIN_DIR/missing" ]
[ ! -e "$(cat "$FIXTURE/stage")" ]
[ "$GOBIN" = preserve-caller-gobin ]
# The installation page lists the same pins.
while IFS=, read -r binary module version release version_package; do
    [ "$binary" = binary ] && continue
    grep -Fxq -- "| \`$binary\` | ${release#v} |" "$ROOT/web/content/docs/installation.mdx" || { echo "installation.mdx does not list $binary ${release#v}"; exit 1; }
done < "$ROOT/scripts/cli-releases.csv"
printf 'CLI installer tests passed: release archives, upgrades in place, version parsing, source fallback, failure cleanup, documented pins\n'
