#!/usr/bin/env bash

set -uo pipefail

RECKON_SETUP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

INSTALLED=0
ALREADY=0
FAILED=0
SKIPPED=0

PLATFORM=""
DISTRO=""
PKG=""
ARCH=""
SUDO=""
GOBIN_DIR=""

if [ -t 1 ]; then
    C_RESET=$'\033[0m'
    C_RED=$'\033[0;31m'
    C_GREEN=$'\033[0;32m'
    C_YELLOW=$'\033[0;33m'
    C_BLUE=$'\033[0;34m'
    C_BOLD=$'\033[1m'
else
    C_RESET=''; C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''; C_BOLD=''
fi

ok()     { printf "  %s✓%s %s\n" "$C_GREEN" "$C_RESET" "$*"; }
info()   { printf "  %s→%s %s\n" "$C_BLUE" "$C_RESET" "$*"; }
warn()   { printf "  %s⚠%s %s\n" "$C_YELLOW" "$C_RESET" "$*"; }
err()    { printf "  %s✗%s %s\n" "$C_RED" "$C_RESET" "$*" >&2; }
header() { printf "\n%s%s%s\n" "$C_BOLD" "$*" "$C_RESET"; }

have() { command -v "$1" >/dev/null 2>&1; }


detect_platform() {
    header "Pre-flight"

    local uname_s arch
    uname_s="$(uname -s)"
    arch="$(uname -m)"

    case "$arch" in
        x86_64|amd64) ARCH=amd64 ;;
        arm64|aarch64) ARCH=arm64 ;;
        *) ARCH="$arch"; warn "Unrecognized arch: $arch — some downloads may fail" ;;
    esac

    case "$uname_s" in
        Darwin)
            PLATFORM=macos
            ok "Platform: macOS ($(sw_vers -productVersion 2>/dev/null || uname -r), $ARCH)"
            ;;
        Linux)
            PLATFORM=linux
            detect_linux_distro
            ;;
        *)
            err "Unsupported OS: $uname_s"
            err "On Windows, run scripts/setup.ps1 in PowerShell (or use WSL2 + this script)."
            exit 1
            ;;
    esac

    if [ "$EUID" -ne 0 ] && have sudo; then
        SUDO="sudo"
    fi
}

detect_linux_distro() {
    if [ ! -r /etc/os-release ]; then
        err "Cannot detect Linux distro (no /etc/os-release)"
        exit 1
    fi
    # shellcheck disable=SC1091
    . /etc/os-release
    local id_all="${ID:-} ${ID_LIKE:-}"
    case " $id_all " in
        *" debian "*|*" ubuntu "*) DISTRO=debian; PKG=apt ;;
        *" fedora "*|*" rhel "*|*" centos "*|*" rocky "*|*" almalinux "*) DISTRO=rhel; PKG=dnf ;;
        *) DISTRO=unknown; PKG="" ;;
    esac
    ok "Linux: ${PRETTY_NAME:-$ID} ($ARCH)"

    if [ -z "$PKG" ]; then
        warn "Distro '${ID:-?}' is not auto-supported by this script."
        warn "Tools that need a package manager will be skipped."
        warn "Install them manually using your distro's package manager."
    else
        ok "Package manager: $PKG"
    fi
}


pkg_install() {
    local apt_pkg="$1" dnf_pkg="$2"
    case "$PKG" in
        apt)
            $SUDO apt-get update -qq >/dev/null 2>&1 || true
            $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$apt_pkg" >/dev/null 2>&1
            ;;
        dnf)
            $SUDO dnf install -y -q "$dnf_pkg" >/dev/null 2>&1
            ;;
        *)
            return 2
            ;;
    esac
}

brew_install() {
    brew install --quiet "$1" >/dev/null 2>&1
}

brew_install_keg() {
    brew install --quiet "$1" >/dev/null 2>&1 && brew link --force --quiet "$1" >/dev/null 2>&1
}

download_install_bin() {
    local url="$1" archive="$2" inner="$3" final="$4" checksum_url="${5:-}"
    local target_dir="${6:-${HOME}/.local/bin}" tmp package_name package_file
    mkdir -p "$target_dir"
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"; trap - RETURN' RETURN

    package_name="${url##*/}"
    case "$archive" in
        zip) package_file="$tmp/pkg.zip" ;;
        tar.gz) package_file="$tmp/pkg.tar.gz" ;;
        none) package_file="$tmp/$final" ;;
        *) return 1 ;;
    esac
    curl -fsSL "$url" -o "$package_file" || return 1

    if [ -n "$checksum_url" ]; then
        local expected actual
        curl -fsSL "$checksum_url" -o "$tmp/checksums.txt" || return 1
        expected="$(awk -v name="$package_name" '$2 == name || $2 == "*" name {print $1; exit}' "$tmp/checksums.txt")"
        [ -n "$expected" ] || { err "checksum for $package_name not found"; return 1; }
        if have sha256sum; then
            actual="$(sha256sum "$package_file" | awk '{print $1}')"
        else
            actual="$(shasum -a 256 "$package_file" | awk '{print $1}')"
        fi
        [ "$actual" = "$expected" ] || { err "checksum mismatch for $package_name"; return 1; }
    fi

    case "$archive" in
        zip)
            ( cd "$tmp" && unzip -q pkg.zip ) || return 1
            install -m 0755 "$tmp/$inner" "$target_dir/$final" || return 1
            ;;
        tar.gz)
            ( cd "$tmp" && tar -xzf pkg.tar.gz ) || return 1
            install -m 0755 "$tmp/$inner" "$target_dir/$final" || return 1
            ;;
        none)
            install -m 0755 "$package_file" "$target_dir/$final" || return 1
            ;;
    esac

    case ":$PATH:" in
        *":$target_dir:"*) : ;;
        *) warn "$final installed to $target_dir but it's NOT on PATH — add to your shell rc:"
           printf '       export PATH="%s:$PATH"\n' "$target_dir" ;;
    esac
}

mark_installed() { ok "$1 — installed"; INSTALLED=$((INSTALLED + 1)); }
mark_already()   { ok "$1 — already installed"; ALREADY=$((ALREADY + 1)); }
mark_failed()    { err "$1 — install failed"; FAILED=$((FAILED + 1)); }
mark_skipped()   { warn "$1 — skipped (manual install required)"; SKIPPED=$((SKIPPED + 1)); }


build_reckon() {
    if ! have cargo; then
        err 'Rust is required to build Reckon from source. Install it from https://rustup.rs, then rerun setup.'
        return 1
    fi
    info 'Building the native Reckon CLI...'
    cargo build --locked --release
}

install_jq() {
    have jq && { mark_already jq; return; }
    info "jq — installing..."
    case "$PLATFORM" in
        macos) brew_install jq && mark_installed jq || mark_failed jq ;;
        linux)
            if [ -n "$PKG" ] && pkg_install jq jq; then
                mark_installed jq
            else
                mark_skipped jq
            fi
            ;;
    esac
}

install_aws() {
    have aws && { mark_already aws; return; }
    info "aws — installing..."
    case "$PLATFORM" in
        macos)
            brew_install awscli && mark_installed aws || mark_failed aws
            ;;
        linux)
            if [ -n "$PKG" ] && pkg_install awscli awscli; then
                mark_installed aws
            else
                mark_skipped aws
                warn "Install AWS CLI v2 manually and verify its PGP signature per AWS documentation."
            fi
            ;;
    esac
}

install_gh() {
    have gh && { mark_already gh; return; }
    info "gh — installing..."
    case "$PLATFORM" in
        macos)
            brew_install gh && mark_installed gh || mark_failed gh
            ;;
        linux)
            case "$PKG" in
                apt)
                    if ( $SUDO mkdir -p -m 755 /etc/apt/keyrings \
                         && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
                              | $SUDO tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null \
                         && $SUDO chmod 644 /etc/apt/keyrings/githubcli-archive-keyring.gpg \
                         && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
                              | $SUDO tee /etc/apt/sources.list.d/github-cli.list >/dev/null \
                         && pkg_install gh gh ); then
                        mark_installed gh
                    else
                        mark_failed gh
                    fi
                    ;;
                dnf)
                    if $SUDO dnf install -y -q 'dnf-command(config-manager)' >/dev/null 2>&1 \
                       && $SUDO dnf config-manager --add-repo https://cli.github.com/packages/rpm/gh-cli.repo >/dev/null 2>&1 \
                       && pkg_install gh gh; then
                        mark_installed gh
                    else
                        mark_failed gh
                    fi
                    ;;
                *) mark_skipped gh ;;
            esac
            ;;
    esac
}

install_kcat() {
    have kcat && { mark_already kcat; return; }
    info "kcat — installing..."
    case "$PLATFORM" in
        macos) brew_install kcat && mark_installed kcat || mark_failed kcat ;;
        linux)
            if [ "$PKG" = apt ]; then
                if pkg_install kcat kcat 2>/dev/null || pkg_install kafkacat kafkacat; then
                    mark_installed kcat
                    have kafkacat && ! have kcat && warn "binary is named 'kafkacat' on this distro"
                else
                    mark_failed kcat
                fi
            elif [ "$PKG" = dnf ]; then
                if pkg_install kcat kcat; then mark_installed kcat; else mark_failed kcat; fi
            else
                mark_skipped kcat
            fi
            ;;
    esac
}

install_rpk() {
    have rpk && { mark_already rpk; return; }
    info "rpk — installing..."
    case "$PLATFORM" in
        macos)
            brew_install redpanda-data/tap/redpanda && mark_installed rpk || mark_failed rpk
            ;;
        linux)
            local url checksum_url version="v26.2.2"
            case "$ARCH" in
                amd64) url="https://github.com/redpanda-data/redpanda/releases/download/${version}/rpk-linux-amd64.zip" ;;
                arm64) url="https://github.com/redpanda-data/redpanda/releases/download/${version}/rpk-linux-arm64.zip" ;;
                *) mark_skipped rpk; return ;;
            esac
            checksum_url="https://github.com/redpanda-data/redpanda/releases/download/${version}/rpk_26.2.2_checksums.txt"
            if download_install_bin "$url" zip rpk rpk "$checksum_url"; then
                mark_installed rpk
            else
                mark_failed rpk
            fi
            ;;
    esac
}

install_mongosh() {
    have mongosh && { mark_already mongosh; return; }
    info "mongosh — installing..."
    case "$PLATFORM" in
        macos) brew_install mongosh && mark_installed mongosh || mark_failed mongosh ;;
        linux)
            if [ -n "$PKG" ] && pkg_install mongodb-mongosh mongodb-mongosh; then
                mark_installed mongosh
            else
                mark_skipped mongosh
                warn "Configure MongoDB's signed package repository, then install mongodb-mongosh."
            fi
            ;;
    esac
}

install_psql() {
    have psql && { mark_already psql; return; }
    info "psql — installing..."
    case "$PLATFORM" in
        macos)
            brew_install_keg libpq && mark_installed psql || mark_failed psql
            ;;
        linux)
            local apt_pkg="postgresql-client" dnf_pkg="postgresql"
            if [ -n "$PKG" ] && pkg_install "$apt_pkg" "$dnf_pkg"; then
                mark_installed psql
            else
                mark_skipped psql
            fi
            ;;
    esac
}

install_mysql() {
    have mysql && { mark_already mysql; return; }
    info "mysql — installing..."
    case "$PLATFORM" in
        macos)
            brew_install_keg mysql-client && mark_installed mysql || mark_failed mysql
            ;;
        linux)
            local apt_pkg="default-mysql-client" dnf_pkg="mysql"
            if [ "$PKG" = apt ]; then
                if pkg_install "$apt_pkg" "$dnf_pkg" || pkg_install mariadb-client mariadb-client; then
                    mark_installed mysql
                else
                    mark_failed mysql
                fi
            elif [ "$PKG" = dnf ]; then
                if pkg_install "$apt_pkg" "$dnf_pkg"; then mark_installed mysql; else mark_failed mysql; fi
            else
                mark_skipped mysql
            fi
            ;;
    esac
}

install_clickhouse() {
    have clickhouse && { mark_already clickhouse; return; }
    info "clickhouse — installing client..."
    case "$PLATFORM" in
        macos)
            brew_install clickhouse && mark_installed clickhouse || mark_failed clickhouse
            ;;
        linux)
            case "$PKG" in
                apt)
                    local deb_arch
                    deb_arch="$(dpkg --print-architecture)"
                    if ( $SUDO apt-get update -qq >/dev/null 2>&1 \
                         && $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
                              apt-transport-https ca-certificates curl gnupg >/dev/null 2>&1 \
                         && { [ -f /usr/share/keyrings/clickhouse-keyring.gpg ] \
                              || curl -fsSL https://packages.clickhouse.com/rpm/lts/repodata/repomd.xml.key \
                                   | $SUDO gpg --dearmor -o /usr/share/keyrings/clickhouse-keyring.gpg; } \
                         && echo "deb [signed-by=/usr/share/keyrings/clickhouse-keyring.gpg arch=${deb_arch}] https://packages.clickhouse.com/deb stable main" \
                              | $SUDO tee /etc/apt/sources.list.d/clickhouse.list >/dev/null \
                         && pkg_install clickhouse-client clickhouse-client ); then
                        mark_installed clickhouse
                    else
                        mark_failed clickhouse
                    fi
                    ;;
                dnf)
                    if $SUDO dnf install -y -q 'dnf-command(config-manager)' >/dev/null 2>&1 \
                       && { [ -f /etc/yum.repos.d/clickhouse.repo ] \
                            || $SUDO dnf config-manager --add-repo https://packages.clickhouse.com/rpm/clickhouse.repo >/dev/null 2>&1; } \
                       && pkg_install clickhouse-client clickhouse-client; then
                        mark_installed clickhouse
                    else
                        mark_failed clickhouse
                    fi
                    ;;
                *)
                    mark_skipped clickhouse
                    warn "Install the official ClickHouse client package or verified official binary."
                    ;;
            esac
            ;;
    esac
}

install_kubectl() {
    have kubectl && { mark_already kubectl; return; }
    info "kubectl — installing..."
    case "$PLATFORM" in
        macos)
            brew_install kubernetes-cli && mark_installed kubectl || mark_failed kubectl
            ;;
        linux)
            local arch
            case "$ARCH" in
                arm64|aarch64) arch="arm64" ;;
                *)             arch="amd64" ;;
            esac
            local ver
            ver="$(curl -fsSL https://dl.k8s.io/release/stable.txt 2>/dev/null)" || { mark_failed kubectl; return; }
            local url checksum expected actual tmp
            url="https://dl.k8s.io/release/${ver}/bin/linux/${arch}/kubectl"
            tmp="$(mktemp -d)"
            if curl -fsSL "$url" -o "$tmp/kubectl" \
               && curl -fsSL "${url}.sha256" -o "$tmp/kubectl.sha256"; then
                expected="$(tr -d '[:space:]' < "$tmp/kubectl.sha256")"
                if have sha256sum; then actual="$(sha256sum "$tmp/kubectl" | awk '{print $1}')";
                else actual="$(shasum -a 256 "$tmp/kubectl" | awk '{print $1}')"; fi
            else
                actual="download-failed"; expected=""
            fi
            if [ -n "$expected" ] && [ "$actual" = "$expected" ] \
               && install -D -m 0755 "$tmp/kubectl" "$HOME/.local/bin/kubectl"; then
                mark_installed kubectl
            else
                mark_failed kubectl
            fi
            rm -rf "$tmp"
            ;;
    esac
}

install_redis_cli() {
    have redis-cli && { mark_already redis-cli; return; }
    info "redis-cli — installing..."
    case "$PLATFORM" in
        macos)
            brew_install redis && mark_installed redis-cli || mark_failed redis-cli
            ;;
        linux)
            if pkg_install redis-tools redis; then
                mark_installed redis-cli
            else
                mark_skipped redis-cli
            fi
            ;;
    esac
}


# shellcheck source=lib/go-clis.sh
source "$RECKON_SETUP_DIR/lib/go-clis.sh"


ensure_brew() {
    if have brew; then
        ok "Homebrew installed ($(brew --version | head -1 | awk '{print $2}'))"
        return
    fi
    err "Homebrew is required on macOS. Install with:"
    echo '    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    exit 1
}

ensure_go() {
    if have go; then
        ok "Go installed ($(go version | awk '{print $3}'))"
    else
        warn "Go not installed — used to build the custom CLIs when no release archive fits"
        info "Installing go..."
        case "$PLATFORM" in
            macos)
                if brew_install go; then
                    mark_installed go
                else
                    err "go install failed"; exit 1
                fi
                ;;
            linux)
                if [ -n "$PKG" ]; then
                    local apt_pkg="golang-go" dnf_pkg="golang"
                    if pkg_install "$apt_pkg" "$dnf_pkg"; then
                        mark_installed go
                    else
                        err "go install failed via $PKG — install manually and re-run"; exit 1
                    fi
                else
                    err "go install requires a supported package manager — install manually"; exit 1
                fi
                ;;
        esac
    fi

    GOBIN_DIR="$(go env GOBIN)"
    [ -n "$GOBIN_DIR" ] || GOBIN_DIR="$(go env GOPATH)/bin"
    case ":$PATH:" in
        *":$GOBIN_DIR:"*) ok "$GOBIN_DIR is on PATH" ;;
        *) warn "$GOBIN_DIR is NOT on PATH — add to your shell rc:"
           echo '       export PATH="$(go env GOPATH)/bin:$PATH"' ;;
    esac
}


setup_workspace() {
    header "Reckon home"
    local reckon_home="${RECKON_HOME:-$HOME/.reckon}"
    (umask 077 && mkdir -p "$reckon_home/bin")
    chmod 0700 "$reckon_home"
    ln -sfn "$(pwd)/target/release/reckon" "$reckon_home/bin/reckon"
    ok "reckon command linked at $reckon_home/bin/reckon"
    case ":$PATH:" in
        *":$reckon_home/bin:"*) ok "$reckon_home/bin is on PATH" ;;
        *) warn "$reckon_home/bin is NOT on PATH; add to your shell rc:"
           printf '       export PATH="%s/bin:$PATH"\n' "$reckon_home" ;;
    esac
}

print_next_steps() {
    header "Next steps"
    cat <<'NEXT'
  1. Create an org for each company or team, or bring this checkout's old layout over:
       reckon org add acme --envs prod,uat,dev
       reckon migrate --org acme --rename production=prod --dry-run
  2. Connect its tools (credentials go in the file it prints, never in chat):
       reckon connect jenkins-prod --org acme --provider jenkins --for-env prod
       reckon connect cubeapm --org acme --provider cubeapm --label prod=PROD --label uat=UAT
  3. Check them, then start the agent pinned to one environment:
       reckon use acme/prod
       reckon verify
       reckon open
  4. Read WORKSPACE.md "Databases, queues and caches" before connecting a database.

NEXT
}


main() {
    cd "$RECKON_SETUP_DIR/.."

    printf "%s=== reckon setup ===%s\n" "$C_BOLD" "$C_RESET"
    printf "Repo: %s\n" "$(pwd)"

    detect_platform

    case "$PLATFORM" in
        macos) ensure_brew ;;
    esac
    build_reckon
    ensure_go

    header "Observability & CI/CD"
    install_jq
    install_aws
    install_gh

    header "Kafka"
    install_kcat
    install_rpk

    header "Database clients"
    install_mongosh
    install_psql
    install_mysql
    install_clickhouse

    header "Kubernetes & cache"
    install_kubectl
    install_redis_cli

    header "Custom CLIs (pinned in scripts/cli-releases.csv)"
    local binary module version release version_package
    while IFS=, read -r binary module version release version_package; do
        [ "$binary" = "binary" ] && continue
        install_go_cli "$module" "$binary" "$version" "$release" "$version_package"
    done < "$RECKON_SETUP_DIR/cli-releases.csv"

    setup_workspace

    header "Summary"
    printf "  %s%d%s newly installed\n" "$C_GREEN" "$INSTALLED" "$C_RESET"
    printf "  %s%d%s already installed\n" "$C_BLUE"  "$ALREADY"   "$C_RESET"
    if [ "$SKIPPED" -gt 0 ]; then
        printf "  %s%d%s skipped (manual install required on this platform)\n" "$C_YELLOW" "$SKIPPED" "$C_RESET"
    fi
    if [ "$FAILED" -gt 0 ]; then
        printf "  %s%d%s failed\n" "$C_RED" "$FAILED" "$C_RESET"
    fi

    print_next_steps

    [ "$FAILED" -eq 0 ]
}

main "$@"
