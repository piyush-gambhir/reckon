#!/usr/bin/env bash
# Exercise the installer without downloading tools or changing the user's PATH.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../scripts/lib/go-clis.sh
source "$ROOT/scripts/lib/go-clis.sh"
FIXTURE="$(mktemp -d)"
trap 'rm -rf -- "$FIXTURE"' EXIT
GOBIN_DIR="$FIXTURE/bin with spaces"
GOBIN="preserve-caller-gobin"
INSTALLED=0
FAILED=0
ALREADY=0
have() { [ -x "$GOBIN_DIR/$1" ]; }
info() { :; }
warn() { :; }
mark_already() { ALREADY=$((ALREADY + 1)); }
mark_installed() { INSTALLED=$((INSTALLED + 1)); }
mark_failed() { FAILED=$((FAILED + 1)); }
go() {
    printf '%s\n' "$@" > "$FIXTURE/args"
    printf '%s\n' "$GOBIN" > "$FIXTURE/stage"
    [ "${FAIL_DOWNLOAD:-0}" -eq 0 ] || return 1
    touch "$GOBIN/cli-go"
    chmod +x "$GOBIN/cli-go"
}
while IFS=, read -r binary module version release version_package; do
    [ "$binary" = binary ] && continue
    [[ "$module" == */cli-go ]]
    install_go_cli "$module" "$binary" "$version" "$release" "$version_package"
    [ -x "$GOBIN_DIR/$binary" ]
    [ ! -e "$GOBIN_DIR/cli-go" ]
    grep -Fxq -- "$module@$version" "$FIXTURE/args"
    grep -Fxq -- "-X $module/$version_package.Version=${release#v}" "$FIXTURE/args"
    [ ! -e "$(cat "$FIXTURE/stage")" ]
    [ "$GOBIN" = preserve-caller-gobin ]
done < "$ROOT/scripts/cli-releases.csv"
[ "$INSTALLED" -eq 4 ]
install_go_cli example/cli-go grafana ignored ignored internal/build
[ "$ALREADY" -eq 1 ]
FAIL_DOWNLOAD=1
install_go_cli example/cli-go missing version release internal/build
[ "$FAILED" -eq 1 ]
[ ! -e "$GOBIN_DIR/missing" ]
[ ! -e "$(cat "$FIXTURE/stage")" ]
[ "$GOBIN" = preserve-caller-gobin ]
printf 'CLI installer tests passed: four named binaries, idempotency, failure cleanup, GOBIN preservation\n'
