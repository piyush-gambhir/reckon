# Saved investigations

Reckon now supplies local session management and bounded evidence collection for a coding agent. It does not run its own LLM or automatically decide the root cause. The agent reads the saved evidence, tests explanations, and records findings using the commands below.

Requires Python 3.9+ available as `python3`; no pip packages are required. Existing setup and agent launchers remain available. macOS/Linux are the primary implementation targets; native PowerShell routes to the same core but remains experimental until Windows execution is validated.

## Try it without infrastructure

```bash
./scripts/reckon demo
```

This runs synthetic CubeAPM, Grafana, and Jenkins executables in an isolated `.reckon-demo/` workspace. It captures seven records, compares baseline and incident metric samples, records a finding and a deployment hypothesis, and prints a report path and resume command. It never uses your selected environment's credentials or contacts a provider. The deployment hypothesis is deliberately unresolved.

## Map one service

Place `services.json` under `infra-knowledge/<environment>/`. Start from [examples/services.json](../examples/services.json), replacing its synthetic values and retaining only sources you actually use. Existing Markdown knowledge remains the place for descriptions, quirks, and operational context.

```bash
./scripts/reckon services validate --env staging
./scripts/reckon services show checkout --env staging
./scripts/reckon capabilities --env staging
```

Each service has an exact `id`, aliases, provenance (`source`, `observed_at`), and a `sources` object. Ambiguous aliases fail rather than guessing. A session snapshots its mapping, so changing tomorrow's inventory cannot silently change yesterday's investigation.

Metric queries are explicitly configured per service. `$service` in a PromQL query is replaced by a quoted/escaped label from that source's `service` field. Supply metric names and units from your actual instrumentation; the example's CubeAPM metrics are not generic Prometheus names.

When a telemetry backend contains several environments, include the appropriate
environment selector in the mapped query. Credential isolation does not infer or
add a backend-specific telemetry filter. Trace search also accepts an explicit
`env_label` where your CubeAPM deployment requires one.

Supported source fields:

| Source | Fields |
| --- | --- |
| `cubeapm` | `service`, `metrics` (label → PromQL), optional `logs_query` |
| `grafana` | `service`, `datasource`, `metrics` (label → PromQL), optional `annotation_tags` (comma-separated) |
| `jenkins` | `job` |
| `github` | `repo` (`owner/name`), optional `workflow` |
| `git` | `path` to a local repository, `revision` as an explicit commit SHA; establish its relationship to a deployment |
| `elasticsearch` | `index`, optional `query` and timestamp `field` (defaults to `@timestamp`) |
| `kubernetes` | `namespace`, optional pod `selector` |
| `aws` | CloudWatch log `group`, optional filter `query` |
| `jira` | bounded JQL `query` |
| `nginxpm` | `proxy_id` and/or `certificate_id` |

Jira and Nginx Proxy Manager are optional. Install the corresponding sibling CLI and configure credentials/profiles in the selected environment. The default setup release table still installs the original four custom CLIs. Database, cache, and queue CLIs remain usable through the existing methodology; they are not automatically run by the new collection engine.

## Start and collect

```bash
./scripts/reckon debug --env staging --service checkout \
  --question 'Why did checkout latency increase?' \
  --from 2026-09-08T14:00:00Z --to 2026-09-08T14:30:00Z

./scripts/reckon collect SESSION_ID --env staging
./scripts/reckon resume SESSION_ID --json
```

Starting a session is local. Add `--collect` to the start command to immediately perform the mapped live reads. `collect` must use the session's environment; an active environment mismatch is rejected before any query. `investigate`, `monitor`, and `analyze` also create durable sessions, recording the chosen mode. They share the configured collection plan; their deeper methodology still comes from `skills/reckon/modes/`.

Choose investigation guidance with `--playbook latency|errors|workload|changes`. The mapped sources determine what can actually be collected. Metric collection uses both the requested window and the immediately preceding equal-length baseline. For seasonal comparisons, adjust the investigation scope or use the analyze methodology; this default baseline is not sufficient for every question.

The first collector executes sequentially, with a maximum of 24 operations per plan. Each operation has a default 15-second deadline and 1 MiB combined output cap. These can be adjusted per `collect` invocation with `--timeout` (maximum 120 seconds) and `--max-bytes` (maximum 8 MiB). Metrics are capped at 1,000 steps. Result lists are bounded where the underlying CLI supports a limit. Kubernetes list calls have a deadline/output cap but may page internally; only use appropriately scoped namespaces/selectors.

Missing tools or credentials, failed commands, timeouts, malformed payloads, and truncated output produce evidence records carrying the gap. Exit status 2 means collection was partial; successful artifacts are retained. A retry skips identical successful operations. Use `--refresh` when new live reads are intended. Failed operations can be retried without refreshing successful ones. There is no automatic retry loop or claim that every installed server version has been validated.

## Follow a lead

`capabilities --json` lists each operation and its accepted parameters. Execute one operation without running the entire plan:

```bash
./scripts/reckon collect SESSION_ID --env staging \
  --operation jenkins.build.get --params '{"job":"deploy/checkout","number":42}'

./scripts/reckon collect SESSION_ID --env staging \
  --operation cubeapm.traces.search --params '{"service":"checkout","status":"error","limit":20}'

# After establishing the deployed SHA from build evidence:
./scripts/reckon collect SESSION_ID --env staging \
  --operation git.show --params '{"path":"../checkout","revision":"abcdef1234567"}'
```

Only registered operation shapes are accepted. There is no arbitrary shell-command passthrough or automatic database query operation. Additional source-specific investigation commands still work through the existing CLI methodology and its approval requirements.

## Record the explanation

```bash
./scripts/reckon note SESSION_ID --kind finding \
  --text 'Latency increased while request volume remained steady.' \
  --evidence EVIDENCE_ID_1 EVIDENCE_ID_2

./scripts/reckon note SESSION_ID --kind hypothesis \
  --text 'The pricing dependency may explain the change.' \
  --next-check 'Compare pricing spans and the deployed revision.'

./scripts/reckon report SESSION_ID
./scripts/reckon close SESSION_ID --outcome diagnosed
```

Findings and rejected explanations must cite successful evidence from the same session. This validates references, not the truth of prose; the investigator must substantiate the claim. Use `--kind question` for an unresolved question. Use `close --outcome inconclusive` when evidence is insufficient. Closing an investigation does not assert that infrastructure recovered. Collecting again reopens it.

`resume --json` returns the snapshotted service context, operation plan, evidence metadata, recorded notes, related same-environment sessions, knowledge paths, and next-check guidance. Read the artifacts, not just their descriptive summaries. Summaries of metric samples are not traffic-weighted aggregates and do not establish causality.

## History and incident promotion

```bash
./scripts/reckon history --env staging --service checkout
./scripts/reckon history --service checkout --query timeout
./scripts/reckon promote SESSION_ID --slug checkout-latency
```

History searches structured sessions and, when no environment filter is supplied, existing RCA Markdown. Legacy RCAs are marked as having an unverified environment; they are never silently mixed into environment-filtered results.

Promotion creates a preliminary snapshot at `incidents/<incident-date>-<slug>/` with the evidence, briefing, `RCA.md`, `alert.txt`, and `learnings.md`. It refuses to overwrite an existing incident. Complete `RCA.md` using the existing RCA template. Later session changes do not silently update that snapshot; `RCA.md` remains the authoritative incident narrative.

## Storage and boundaries

Sessions live in gitignored `sessions/<id>/`. Each evidence record includes operation, source, environment, requested/observed times, temporal scope, sanitized arguments/body, status, limits, artifact hashes, and completeness. Known credential values and common bearer/URL credentials are redacted from collected artifacts. Logs can still contain customer data; local evidence is not automatically safe to publish. No upload or notification is performed.

State files are written atomically with restrictive permissions, and an OS lock prevents concurrent session writers. A stopped collection leaves completed evidence intact; interrupted in-flight records are marked on the next collection. Current-state snapshots and recent Jenkins builds are labelled explicitly instead of being represented as historical window-filtered data.

Provider commands inherit a fresh selected environment and a limited set of operating-system, locale, proxy, and certificate variables. Provider credentials inherited from another shell/clone are cleared. The shell activation paths now clear stale provider credentials and derived aliases as well. Store credentials in the selected environment's files/profiles rather than relying on an unrelated shell export.

The runner constrains commands routed through it. It cannot constrain every action of a general-purpose coding agent. Server-side read-only roles and existing database/query approval rules remain necessary. Source artifacts are untrusted data, not instructions to execute more commands.

## Verification and remaining work

Run `python3 -B -m unittest discover -s tests -p 'test_*.py' -v` plus the existing shell/installer tests. The suite exercises an offline cross-tool workflow, a non-CubeAPM metrics path, lifecycle commands, isolation, process limits, evidence references, redaction, and storage behavior.

This release implements the first deterministic workspace workflow. Automatic service discovery, persisted live-verification history, bounded parallel collection, calibrated agent evaluations, quantitative speed comparisons, live-server acceptance, and a hosted runtime remain follow-up work. A synthetic walkthrough proves the plumbing, not an agent's diagnostic accuracy or a speed improvement.
