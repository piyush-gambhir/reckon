#!/usr/bin/env bash
# Public release pins live in ../cli-releases.csv, shared with setup.ps1.
# Nested cli-go/v* module tags match the public root release tags.

install_go_cli() {
    local module="$1" bin="$2" version="$3" release="$4" version_package="$5"
    if have "$bin"; then mark_already "$bin"; return; fi
    local stage
    stage="$(mktemp -d)" || { mark_failed "$bin"; return; }
    info "$bin — installing $release (${module}@${version})..."
    # Every main package is called cli-go. Stage it privately, then install it
    # under its public name so the four packages cannot overwrite each other.
    if GOBIN="$stage" go install \
        -ldflags "-X ${module}/${version_package}.Version=${release#v}" \
        "${module}@${version}" \
        && mkdir -p "$GOBIN_DIR" \
        && mv "$stage/cli-go" "$GOBIN_DIR/$bin"; then
        if have "$bin"; then
            mark_installed "$bin"
        else
            warn "$bin — installed to $GOBIN_DIR but not on PATH yet"
            INSTALLED=$((INSTALLED + 1))
        fi
    else
        mark_failed "$bin"
    fi
    rm -rf -- "$stage"
}
