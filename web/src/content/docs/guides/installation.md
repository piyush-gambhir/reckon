---
title: Installation
description: Clone the workspace, run the setup script to install the CLIs, and seed credentials so the agent is ready when an alert fires.
---

`rca-assist` is a *workspace*, not a package. You clone it, run the setup script, fill in real credentials, and `cd` in whenever you need to investigate.

## Prerequisites

- **A package manager** — Homebrew (macOS), `apt`/`dnf` (Linux), or `winget` (Windows). The setup script uses it to install the tools that have native packages.
- **Go** — the three custom CLIs (`grafana`, `jenkins`, `cubeapm`) are installed via `go install`. The setup script installs Go for you if it's missing where it can.
- **Claude Code** — the agent runtime. See the [Claude Code docs](https://docs.anthropic.com/en/docs/claude-code) for install instructions.

## Quick start (macOS / Linux)

```bash
git clone https://github.com/piyush-gambhir/rca-assist.git
cd rca-assist
bash scripts/setup.sh
```

`scripts/setup.sh` is **idempotent** — it installs only what's missing, then seeds `.env` from `.env.example`, seeds `infra-knowledge/*.md` from the `.example.md` templates, and runs `direnv allow`. Re-run it any time.

It installs **twelve read-only CLIs** plus `direnv` and `jq`:

| Group | Tools |
|-------|-------|
| Observability / CI-CD | `grafana`, `jenkins`, `cubeapm` |
| Code & infra cross-check | `gh`, `aws` |
| Message queues (Kafka) | `kcat`, `rpk` |
| Kubernetes & cache | `kubectl`, `redis-cli` |
| Read-only databases | `mongosh`, `psql`, `mysql` |
| Log stores *(optional)* | `es` — Elasticsearch/ELK, read-only enforced via `ES_READ_ONLY` |

## Platforms

| Platform | How tools install | Notes |
|----------|-------------------|-------|
| macOS (Apple Silicon / Intel) | Homebrew + `go install` | First-class; all tools. |
| Linux (Debian/Ubuntu, Fedora/RHEL) | `apt`/`dnf` + upstream installers + `go install` | First-class; all tools. |
| Windows — **WSL2 (recommended)** | inherits the Linux path | First-class; run `bash scripts/setup.sh` inside WSL. |
| Windows — native PowerShell | `winget` + `go install` via `scripts/setup.ps1` | Partial: `direnv`, `kcat`, `rpk` have no Windows port — use WSL2 for those. After install, dot-source `scripts/activate.ps1` each session as the direnv replacement. |

## Seed credentials

The setup script already copied `.env.example` to `.env`. **This clone is for production credentials only** — if you need staging or UAT, use a different clone so the agent never crosses environments during an RCA.

```bash
$EDITOR .env       # fill in real values
direnv allow       # re-allow after editing .envrc or first run
```

`.envrc` sets `XDG_CONFIG_HOME` to `.config/` inside the workspace, so any saved CLI profiles are isolated from your global `~/.config/` ones. Either approach works:

- **Preferred — env vars in `.env`.** Direnv exports them automatically when you `cd` in. No interactive logins, immediately usable. (Quote any value containing `&`, `;`, `$`, or spaces — `.env` is sourced as shell.)
- **Fallback — saved profiles.** Run `grafana login`, `jenkins login`, `cubeapm login`, `aws configure`, or `gh auth login` from inside the workspace and the profiles land under `.config/`.

:::caution[Read-only database access]
The workspace can open production DB shells (`mongosh`, `psql`, `mysql`). Before using them, read the **[Database safety contract](/reference/skill-overview/#database-safety)** — the DB user **must** be a true read-only role, every query should `EXPLAIN` first and `LIMIT` always, and the allowlist must keep these clients behind a per-query approval prompt.
:::

## Skills

The **`rca-assist` skill ships in this repo** at `skills/rca-assist/` and is surfaced to the agent via a symlink — no install step. The three CLI *skills* (`grafana`, `jenkins`, `cubeapm` command references) are pinned in `skills-lock.json` and installed repo-locally; if you need to (re)install them:

```bash
npx skills add grafana jenkins cubeapm
```

> `npx skills add` installs **skills** (markdown command references), not the CLI binaries — the binaries come from `scripts/setup.sh` above.

## Verify

One safe read per tool (skip any you didn't credential):

```bash
grafana user current -o json
jenkins status -o json
cubeapm metrics label-values service -o json | jq -r '.[].VALUE' | head   # canonical inventory; not `traces services`
aws sts get-caller-identity --output json
gh auth status
rpk cluster info                       # RPK_BROKERS / RPK_SASL_* are derived from KAFKA_* by .envrc
kubectl get ns                         # needs KUBECONFIG seeded: aws eks update-kubeconfig --name <cluster>
redis-cli -u "$REDIS_URL" PING
mongosh "$MONGODB_URI" --eval 'db.runCommand({ping:1})'
psql -c "SELECT current_user, pg_is_in_recovery();"
mysql --defaults-extra-file="$XDG_CONFIG_HOME/mysql/my.cnf" -e "SELECT CURRENT_USER(), @@transaction_read_only;"
es cluster health -o json              # optional — only if ES_URL is set
```

If the tools you use return real data, you're set. The next time an alert fires, paste it into the agent and let it run the cascade. See [First investigation](/guides/first-investigation/) for what to expect.

## Agent runtimes & billing

The workspace runs under **Claude Code** (reads `CLAUDE.md` + skills), **Codex CLI**, or **OpenCode** (both read `AGENTS.md`). Launch with `scripts/agent.sh [claude|codex|opencode]` — it auto-detects when called bare.

Billing is per-runtime, pick one path each:

| Runtime | Subscription path (no key needed) | API-key path (set in `.env`) |
|---------|-----------------------------------|------------------------------|
| Claude Code | `claude` → `/login` (Pro/Max) | `ANTHROPIC_API_KEY` |
| Codex CLI | `codex login` (ChatGPT plan) | `OPENAI_API_KEY` |

Keep approval prompts ON in any runtime — the per-query DB approval is a load-bearing safety layer.

## Incidents viewer

Browse your local RCA corpus in a web UI — incident list, rendered markdown, alert text, and evidence files:

```bash
node ui/serve.mjs        # → http://localhost:7777, binds 127.0.0.1 only
```
