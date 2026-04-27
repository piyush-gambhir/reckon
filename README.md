# rca-assist

Root Cause Analysis assistant powered by Grafana, Jenkins, and CubeAPM CLIs.

📖 **Docs site:** see [`web/`](web/) (Astro + Starlight, deployed to Cloudflare Pages). Run `cd web && npm install && npm run dev` for local preview.

## Overview

`rca-assist` is a Claude Code workspace for investigating production incidents and performing root cause analysis. It wires together three observability and CI/CD tools under a single, isolated credential environment so a coding agent can correlate signals across systems in one session.

This clone is intended to be **production-only**. Put only production Grafana, Jenkins, and CubeAPM credentials in this workspace. If you ever need staging or UAT, use a separate clone so the agent never mixes environments during an RCA.

**Tools available to the agent:**

| CLI | Covers |
|-----|--------|
| [`grafana`](https://github.com/piyush-gambhir/grafana-cli) | Dashboards, datasources, alerts, annotations |
| [`jenkins`](https://github.com/piyush-gambhir/jenkins-cli) | Jobs, builds, pipelines, logs, nodes |
| [`cubeapm`](https://github.com/piyush-gambhir/cubeapm-cli) | Distributed traces, PromQL metrics, LogsQL logs |

## Prerequisites

- [direnv](https://direnv.net/) — for folder-scoped credentials
- [grafana-cli](https://github.com/piyush-gambhir/grafana-cli) — `go install github.com/piyush-gambhir/grafana-cli@latest`
- [jenkins-cli](https://github.com/piyush-gambhir/jenkins-cli) — `go install github.com/piyush-gambhir/jenkins-cli@latest`
- [cubeapm-cli](https://github.com/piyush-gambhir/cubeapm-cli) — `go install github.com/piyush-gambhir/cubeapm-cli@latest`

## Setup

```bash
# Clone this workspace
git clone https://github.com/piyush-gambhir/rca-assist.git
cd rca-assist

# Seed folder-local credentials for all three CLIs
cp -n .env.example .env
# Then edit .env (or create .env.local) with your real Grafana, Jenkins,
# and CubeAPM production credentials.

# Allow direnv to load the isolated environment and repo-local secrets
direnv allow

# Optional fallback: if you prefer saved CLI profiles instead of env vars,
# you can still authenticate each tool and their config will stay in .config/
# within this directory.
# grafana login
# jenkins login
# cubeapm login

# Seed the team-specific knowledge files from the committed templates
cd infra-knowledge
for f in *.example.md; do cp -n "$f" "${f/.example/}"; done
cd ..
# Then edit each infra-knowledge/<topic>.md with your real data — they're
# gitignored so your service names, on-call contacts, etc. stay local.
```

Verify all three connections:

```bash
grafana user current -o json
jenkins status -o json
cubeapm traces services -o json
```

## How credentials are isolated

`.envrc` sets `XDG_CONFIG_HOME` to `.config/` inside this directory and also loads `.env` / `.env.local` when present. That gives you two repo-local authentication modes:

- **Preferred:** store credentials in `.env` or `.env.local` and let direnv export them automatically whenever you enter this repo.
- **Fallback:** run `grafana login`, `jenkins login`, or `cubeapm login` inside this directory to save per-repo CLI profiles under `.config/`.

Either way, credentials stay isolated from your global `~/.config/` profiles. You can have different credentials per clone of this repo.

Config files land at:
- `.config/grafana-cli/config.yaml`
- `.config/jenkins-cli/config.yaml`
- `.config/cubeapm-cli/config.yaml`

## Usage with Claude Code

Open this directory in Claude Code (`claude`) and describe the incident. The agent will follow the RCA workflow in `CLAUDE.md` to correlate alerts, traces, metrics, logs, and deployment history across all three tools.

Example prompts:
- *"The checkout service latency spiked at 14:30 UTC. What happened?"*
- *"Which Jenkins build broke the payments pipeline and when did it start?"*
- *"Find all error traces from the auth service in the last hour."*

## RCA Workflow (summary)

1. **Assess** — check Grafana alerts, Jenkins failures, CubeAPM service health
2. **Investigate errors** — search error traces and logs, view trace waterfalls
3. **Check metrics** — error rate, latency percentiles, service uptime
4. **Check deployments** — Jenkins build history, Grafana deploy annotations
5. **Map dependencies** — service dependency graph, datasource health
6. **Correlate** — match deployment timestamps with error spikes across tools

See `CLAUDE.md` for the full agent workflow and exact commands.
