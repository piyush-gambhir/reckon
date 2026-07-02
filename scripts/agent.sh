#!/usr/bin/env bash
# Launch an agent runtime in this workspace.
#
#   scripts/agent.sh              # auto-detect: claude, then codex, then opencode
#   scripts/agent.sh claude       # Claude Code  (subscription login or ANTHROPIC_API_KEY)
#   scripts/agent.sh codex        # Codex CLI    (ChatGPT login or OPENAI_API_KEY)
#   scripts/agent.sh opencode     # OpenCode     (any provider key)
#
# Anything after the runtime name is passed through, e.g.:
#   scripts/agent.sh claude --continue
#
# Each runtime picks up its guide automatically: Claude Code reads CLAUDE.md
# (+ skills); Codex and OpenCode read AGENTS.md. Credentials come from .env
# via direnv — run this from the workspace root so direnv has loaded.

set -euo pipefail
cd "$(cd "$(dirname "$0")" && pwd)/.."

runtime="${1:-}"
[ $# -gt 0 ] && shift

have() { command -v "$1" >/dev/null 2>&1; }

if [ -z "$runtime" ]; then
    for candidate in claude codex opencode; do
        if have "$candidate"; then runtime="$candidate"; break; fi
    done
    if [ -z "$runtime" ]; then
        echo "No agent runtime found on PATH. Install one of:" >&2
        echo "  claude   — npm install -g @anthropic-ai/claude-code   (or see claude.com/claude-code)" >&2
        echo "  codex    — npm install -g @openai/codex" >&2
        echo "  opencode — see opencode.ai" >&2
        exit 1
    fi
    echo "→ launching $runtime (auto-detected)"
fi

case "$runtime" in
    claude)
        have claude || { echo "claude not on PATH — npm install -g @anthropic-ai/claude-code" >&2; exit 1; }
        # Subscription login needs no key; ANTHROPIC_API_KEY from .env enables API billing.
        exec claude "$@"
        ;;
    codex)
        have codex || { echo "codex not on PATH — npm install -g @openai/codex" >&2; exit 1; }
        # Production workspace: keep approvals on. Never pass --full-auto here.
        exec codex "$@"
        ;;
    opencode)
        have opencode || { echo "opencode not on PATH — see opencode.ai" >&2; exit 1; }
        exec opencode "$@"
        ;;
    *)
        echo "Unknown runtime: $runtime (expected claude | codex | opencode)" >&2
        exit 1
        ;;
esac
