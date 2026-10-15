#!/usr/bin/env bash
# Public release pins live in ../cli-releases.csv, shared with setup.ps1.
# Each CLI installs from its release archive, checked against the release's
# checksums.txt: release builds carry settings a source build lacks (gsc's
# built-in OAuth client). go install is the fallback where no archive fits.
# The version column is a nested cli-go/v* module tag where the repo publishes
# one, otherwise the release tag's commit (gsc, bwt, clarity).

# The x.y.z a CLI reports: the number after "version" (gsc also prints the
# latest release it knows of), else the first one in its output.
cli_version() {
    local out found
    out="$(NO_UPDATE_NOTIFIER=1 CI=1 "$1" version </dev/null 2>&1 | head -n 20 || true)"
    found="$(printf '%s\n' "$out" | grep -Eio 'version[[:space:]:=]+v?[0-9]+\.[0-9]+\.[0-9]+' | head -n 1 || true)"
    [ -n "$found" ] || found="$out"
    printf '%s\n' "$found" | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -n 1 || true
}

# True when x.y.z $1 is older than $2.
version_older() {
    local IFS=. i
    local -a a b
    read -r -a a <<< "$1"
    read -r -a b <<< "$2"
    for i in 0 1 2; do
        [ "${a[i]}" -lt "${b[i]}" ] && return 0
        [ "${a[i]}" -gt "${b[i]}" ] && return 1
    done
    return 1
}

# After an install, the copy PATH finds must be current: an older one in a
# directory setup cannot write, earlier on PATH, would still win.
mark_active() {
    local bin="$1" release="$2" found
    hash -r
    found="$(cli_version "$bin")"
    if [ -n "$found" ] && version_older "$found" "${release#v}"; then
        warn "$bin — $(command -v "$bin") ($found) still comes first on PATH; remove it or move it later on PATH"
        mark_failed "$bin"
    else
        mark_installed "$bin"
    fi
}

install_go_cli() {
    local module="$1" bin="$2" version="$3" release="$4" version_package="$5"
    local current="" dir="$HOME/.local/bin" target="$GOBIN_DIR" repo url os=linux
    if have "$bin"; then
        current="$(cli_version "$bin")"
        # A copy that does not say its version is left alone.
        if [ -z "$current" ] || ! version_older "$current" "${release#v}"; then
            mark_already "$bin"; return
        fi
        info "$bin — $current is older than the pinned $release, upgrading..."
        # Replace it where it is, so an old copy cannot shadow the new one.
        if [ -w "$(dirname "$(command -v "$bin")")" ]; then
            dir="$(dirname "$(command -v "$bin")")"
            target="$dir"
        fi
    fi
    [ "$PLATFORM" = macos ] && os=darwin
    repo="${module#github.com/}"
    repo="${repo%/cli-go}"
    url="https://github.com/$repo/releases/download/$release"
    info "$bin — installing $release from github.com/$repo..."
    if download_install_bin "$url/${repo##*/}_${os}_${ARCH}.tar.gz" tar.gz "$bin" "$bin" "$url/checksums.txt" "$dir"; then
        mark_active "$bin" "$release"
        return
    fi
    have go || { mark_failed "$bin"; return; }
    warn "$bin — could not install the $os/$ARCH release archive, building from source (gsc then needs your own OAuth client)"
    local stage
    stage="$(mktemp -d)" || { mark_failed "$bin"; return; }
    # Every main package is called cli-go. Stage it privately, then install it
    # under its public name so the packages cannot overwrite each other.
    if GOBIN="$stage" go install \
        -ldflags "-X ${module}/${version_package}.Version=${release#v}" \
        "${module}@${version}" \
        && mkdir -p "$target" \
        && mv "$stage/cli-go" "$target/$bin"; then
        hash -r
        if have "$bin"; then
            mark_active "$bin" "$release"
        else
            warn "$bin — installed to $target but not on PATH yet"
            INSTALLED=$((INSTALLED + 1))
        fi
    else
        mark_failed "$bin"
    fi
    rm -rf -- "$stage"
}
