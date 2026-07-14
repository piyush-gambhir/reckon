#!/usr/bin/env bash
#
# reckon PDF export — render an incident RCA Markdown document as a PDF.
#
# Usage:
#   bash scripts/export-rca-pdf.sh PATH_TO_RCA_MD [OPTIONAL_OUTPUT_PDF] [--force]
#   bash scripts/export-rca-pdf.sh incidents/SOME_DIR [OPTIONAL_OUTPUT_PDF] [--force]
#
# Idempotent: an existing output is left untouched unless --force is supplied.
#

set -euo pipefail

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

usage() {
    cat <<'EOF'
Usage:
  bash scripts/export-rca-pdf.sh PATH_TO_RCA_MD [OPTIONAL_OUTPUT_PDF] [--force]
  bash scripts/export-rca-pdf.sh incidents/SOME_DIR [OPTIONAL_OUTPUT_PDF] [--force]

The directory form exports incidents/SOME_DIR/RCA.md. By default, the PDF is
written next to the input as RCA.pdf. Existing PDFs require --force.
EOF
}

resolve_path() {
    local path="$1" directory base
    directory="$(cd "$(dirname "$path")" && pwd -P)"
    base="$(basename "$path")"
    printf '%s/%s\n' "$directory" "$base"
}

print_install_hint() {
    err "No supported Markdown-to-PDF renderer is available."
    err "macOS: brew install pandoc weasyprint"
    err "Debian/Ubuntu: sudo apt-get install pandoc weasyprint"
    err "Alternatively install Node.js to use the npx md-to-pdf fallback."
}

main() {
    local force=0 input_arg="" output_arg="" input output output_dir
    local engine="" title heading_count temp_dir generated
    local -a positional=()

    for arg in "$@"; do
        case "$arg" in
            --force) force=1 ;;
            --help|-h) usage; exit 0 ;;
            --*) err "Unknown option: $arg"; usage; exit 1 ;;
            *) positional+=("$arg") ;;
        esac
    done

    if [ "${#positional[@]}" -lt 1 ] || [ "${#positional[@]}" -gt 2 ]; then
        usage
        exit 1
    fi

    input_arg="${positional[0]}"
    output_arg="${positional[1]:-}"

    if [ -d "$input_arg" ]; then
        input_arg="$input_arg/RCA.md"
    fi
    if [ ! -f "$input_arg" ]; then
        err "RCA Markdown file not found: $input_arg"
        exit 1
    fi

    input="$(resolve_path "$input_arg")"
    if [ -n "$output_arg" ]; then
        output="$(resolve_path "$output_arg")"
    else
        output="${input%.md}.pdf"
    fi
    output_dir="$(dirname "$output")"

    if [ -e "$output" ] && [ "$force" -ne 1 ]; then
        err "Output already exists: $output"
        err "Re-run with --force to overwrite it."
        exit 1
    fi
    if [ ! -d "$output_dir" ]; then
        err "Output directory does not exist: $output_dir"
        exit 1
    fi

    title="$(awk '/^#[[:space:]]+[^#]/{sub(/^#[[:space:]]+/, ""); print; exit}' "$input")"
    title="${title:-$(basename "${input%.md}")}"
    heading_count="$(grep -Ec '^#{1,6}[[:space:]]+' "$input" || true)"

    header "Exporting RCA to PDF"
    info "Input: $input"

    if have pandoc; then
        for candidate in weasyprint wkhtmltopdf tectonic xelatex; do
            if have "$candidate"; then
                engine="$candidate"
                break
            fi
        done
    fi

    if [ -n "$engine" ]; then
        local -a pandoc_args=(
            "$input"
            --output "$output"
            "--pdf-engine=$engine"
            -V geometry:margin=2cm
            --highlight-style=tango
            --metadata "title=$title"
        )
        if [ "$heading_count" -ge 3 ]; then
            pandoc_args+=(--toc)
        fi
        info "Using pandoc with $engine"
        pandoc "${pandoc_args[@]}"
    elif have node && have npx; then
        warn "pandoc with a supported PDF engine is unavailable; using npx md-to-pdf"
        temp_dir="$(mktemp -d)"
        trap 'rm -rf "$temp_dir"' EXIT
        npx --yes md-to-pdf "$input" --dest "$temp_dir"
        generated="$temp_dir/$(basename "${input%.md}").pdf"
        if [ ! -f "$generated" ]; then
            err "md-to-pdf completed without creating the expected PDF: $generated"
            exit 1
        fi
        mv -f "$generated" "$output"
    else
        print_install_hint
        exit 1
    fi

    ok "PDF written to: $output"
}

main "$@"
