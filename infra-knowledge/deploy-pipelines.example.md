# Deploy pipelines

Mapping of service → Jenkins job that deploys it → how to check if a deploy shipped during an incident window.

When an RCA needs to answer "was there a deploy at time T?", use this file to know which Jenkins job to look at.

## Format

For each service, ideally record:

- **Service label** (matches [services.md](services.md))
- **Jenkins job name** (what you pass to `jenkins build list <JOB>`)
- **Deploy marker source** — Grafana annotation tag (if the pipeline writes one), or rely on Jenkins build timestamp
- **Typical deploy cadence** — once a day? per PR? ad-hoc?
- **Rollback procedure**

## Entries

*(empty — fill in as you learn. Starter template below.)*

### `<SERVICE-NAME>`

| | |
|---|---|
| Jenkins job | `<job-name>` |
| Grafana annotation tag | `deploy`, `release`, or none |
| Typical cadence | |
| Rollback command | `jenkins build start <rollback-job>` or manual |

## How to check during an RCA

```bash
# 1. Did a deploy ship in the window?
jenkins build list <JOB> -o json | jq '.[] | select(.timestamp > <FROM_MS> and .timestamp < <TO_MS>)'

# 2. If yes, what changed?
jenkins build log <JOB> <BUILD_NUMBER> | head -200

# 3. Cross-reference with Grafana
grafana annotation list --tags deploy --from <FROM_MS> --to <TO_MS> -o json
```

## Notes

- If the user reports an incident correlated with a Jenkins build, pull the commit range from the build log — the first lines usually show the git ref.
- A deploy does not automatically mean the deploy *caused* the incident. Compare the per-call latency series (§3.3 in the skill) to see if the step-change aligns with the build timestamp.
