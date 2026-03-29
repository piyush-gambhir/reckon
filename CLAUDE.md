# RCA Assist

Root Cause Analysis assistant powered by Grafana, Jenkins, and CubeAPM CLIs.

## Purpose

This project is a workspace for spinning up a coding agent to investigate production incidents, debug service issues, and perform root cause analysis. The agent has access to three observability and CI/CD tools:

- **Grafana CLI** (`grafana`) -- dashboards, datasources, alerts, annotations
- **Jenkins CLI** (`jenkins`) -- jobs, builds, pipelines, logs, nodes
- **CubeAPM CLI** (`cubeapm`) -- distributed traces, PromQL metrics, LogsQL logs

## Skills

Skills are installed globally via `npx skills add`. See the `grafana`, `jenkins`, and `cubeapm` skills for full command references and workflows.

## Authentication

This project uses **folder-specific credentials** via `.envrc` which sets `XDG_CONFIG_HOME` to `.config/` within this directory. Credentials stored here are isolated from your global CLI configs.

Make sure direnv is set up (`direnv allow`), then run login for each tool:

```bash
grafana login
jenkins login
cubeapm login
```

Config files are stored at:
- `.config/grafana-cli/config.yaml`
- `.config/jenkins-cli/config.yaml`
- `.config/cubeapm-cli/config.yaml`

Verify all three connections:
```bash
grafana user current -o json
jenkins status -o json
cubeapm traces services -o json
```

## RCA Workflow

When investigating an incident, follow this general approach:

### 1. Assess the situation
- Check **Grafana alerts**: `grafana alert rule list -o json` and `grafana alert silence list -o json`
- Check **Jenkins build status**: `jenkins job list --recursive --status FAILURE -o json`
- Check **CubeAPM services**: `cubeapm traces services -o json`

### 2. Investigate errors (traces + logs)
- Search error traces: `cubeapm traces search --service <svc> --status error --last 1h -o json`
- Get trace waterfall: `cubeapm traces get <trace-id> -o json`
- Search related logs: `cubeapm logs query 'error' --service <svc> --last 1h -o json`
- Check log volume: `cubeapm logs hits --query 'level:error' --last 6h --step 15m -o json`

### 3. Check metrics
- Error rate: `cubeapm metrics query 'rate(http_requests_total{status=~"5.."}[5m])' -o json`
- Latency: `cubeapm metrics query 'histogram_quantile(0.99, sum by (le) (rate(http_duration_seconds_bucket[5m])))' -o json`
- Service health: `cubeapm metrics query 'up' -o json`

### 4. Check recent deployments
- Grafana annotations: `grafana annotation list --tags deploy -o json`
- Jenkins recent builds: `jenkins build list <deploy-job> -o json`
- Build logs: `jenkins build log <job> <number>`

### 5. Check service dependencies
- Dependency graph: `cubeapm traces dependencies --last 1h -o json`
- Grafana datasource health: `grafana datasource list -o json`

### 6. Correlate across tools
- Match deployment timestamps (Jenkins build times) with error spikes (CubeAPM metrics/traces)
- Check Grafana annotations around the incident window
- Trace error propagation through the dependency graph

## Agent Guidelines

- **ALWAYS use `-o json`** for all CLI commands when parsing output programmatically.
- **Start broad, then narrow.** List services first, then drill into the problematic one.
- **Correlate timestamps.** Match deploy times from Jenkins with error spikes in CubeAPM.
- **Use time ranges.** Most CubeAPM commands accept `--last 1h`, `--from`, `--to`.
- **Check multiple signals.** Don't rely on just traces or just metrics -- cross-reference.
- **Document findings.** As you investigate, summarize what you find at each step.
- **Be explicit about uncertainty.** If the data is inconclusive, say so.
