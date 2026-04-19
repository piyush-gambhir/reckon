# rca-assist

Root Cause Analysis assistant powered by Grafana, Jenkins, and CubeAPM CLIs.

## Overview

`rca-assist` is a Claude Code workspace for investigating production incidents and performing root cause analysis. It wires together three observability and CI/CD tools under a single, isolated credential environment so a coding agent can correlate signals across systems in one session.

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

# Allow direnv to load the isolated credential environment
direnv allow

# Authenticate each tool (credentials stored in .config/ within this directory)
grafana login
jenkins login
cubeapm login

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

`.envrc` sets `XDG_CONFIG_HOME` to `.config/` inside this directory. Each CLI reads its config from there, keeping credentials fully isolated from your global `~/.config/` profiles. You can have different credentials per clone of this repo.

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
