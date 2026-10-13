# Saved investigations

Reckon now supplies local session management and bounded evidence collection for a coding agent. It does not run its own LLM or automatically decide the root cause. The agent reads the saved evidence, tests explanations, and records findings using the commands below.

Requires Python 3.9+ available as `python3`; no pip packages are required. Every command works in the pinned org and environment (`reckon preflight` shows them; `--org` and `--env` override). macOS/Linux are the primary implementation targets; native PowerShell routes to the same core but remains experimental until Windows execution is validated.

## Try it without infrastructure

```bash
reckon demo
```

This creates a throwaway org under `~/.reckon/demos/` and runs synthetic CubeAPM, Grafana, and Jenkins executables through real connections. It captures seven records, compares baseline and incident metric samples, records a finding and a deployment hypothesis, and prints a report path and resume command. It never uses your orgs' credentials or contacts a provider. The deployment hypothesis is deliberately unresolved.

## Map one service

Place `services.json` under the org's `knowledge/<environment>/` (in `~/.reckon/orgs/<org>/workspace/`). Start from [examples/services.json](../examples/services.json), replacing its synthetic values and retaining only sources you actually use. Existing Markdown knowledge remains the place for descriptions, quirks, and operational context.

```bash
reckon services validate
reckon services show checkout
reckon capabilities
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

Each source needs a connection for the session's environment (`reckon connect`). Database, cache, and queue tools are not part of typed collection; reach them with `reckon run`, which asks the operator first.

## Start and collect

```bash
reckon debug --service checkout \
  --question 'Why did checkout latency increase?' \
  --from 2026-09-08T14:00:00Z --to 2026-09-08T14:30:00Z

reckon collect SESSION_ID
reckon resume SESSION_ID --json
```

Starting a session is local. Add `--collect` to the start command to immediately perform the mapped live reads. `collect` must run in the session's org and environment; a mismatch is rejected before any query. `investigate`, `monitor`, and `analyze` also create durable sessions, recording the chosen mode. They share the configured collection plan; their deeper methodology still comes from `skills/reckon/modes/`.

Choose investigation guidance with `--playbook latency|errors|workload|changes`. The mapped sources determine what can actually be collected. Metric collection uses both the requested window and the immediately preceding equal-length baseline. For seasonal comparisons, adjust the investigation scope or use the analyze methodology; this default baseline is not sufficient for every question.

The first collector executes sequentially, with a maximum of 24 operations per plan. Each operation has a default 15-second deadline and 1 MiB combined output cap. These can be adjusted per `collect` invocation with `--timeout` (maximum 120 seconds) and `--max-bytes` (maximum 8 MiB). Metrics are capped at 1,000 steps. Result lists are bounded where the underlying CLI supports a limit. Kubernetes list calls have a deadline/output cap but may page internally; only use appropriately scoped namespaces/selectors.

Missing tools or credentials, failed commands, timeouts, malformed payloads, and truncated output produce evidence records carrying the gap. Exit status 2 means collection was partial; successful artifacts are retained. A retry skips identical successful operations. Use `--refresh` when new live reads are intended. Failed operations can be retried without refreshing successful ones. There is no automatic retry loop or claim that every installed server version has been validated.

## Follow a lead

`capabilities --json` lists each operation and its accepted parameters. Execute one operation without running the entire plan:

```bash
reckon collect SESSION_ID \
  --operation jenkins.build.get --params '{"job":"deploy/checkout","number":42}'

reckon collect SESSION_ID \
  --operation cubeapm.traces.search --params '{"service":"checkout","status":"error","limit":20}'

# After establishing the deployed SHA from build evidence:
reckon collect SESSION_ID \
  --operation git.show --params '{"path":"../checkout","revision":"abcdef1234567"}'
```

Only registered operation shapes are accepted. For any other read, use `reckon run <connection> --session SESSION_ID -- <arguments>`: the output is saved as evidence in the same session, with its command and connection.

## Record the explanation

```bash
reckon note SESSION_ID --kind finding \
  --text 'Latency increased while request volume remained steady.' \
  --evidence EVIDENCE_ID_1 EVIDENCE_ID_2

reckon note SESSION_ID --kind hypothesis \
  --text 'The pricing dependency may explain the change.' \
  --next-check 'Compare pricing spans and the deployed revision.'

reckon report SESSION_ID
reckon close SESSION_ID --outcome diagnosed
```

Findings and rejected explanations must cite successful evidence from the same session. This validates references, not the truth of prose; the investigator must substantiate the claim. Use `--kind question` for an unresolved question. Use `close --outcome inconclusive` when evidence is insufficient. Closing an investigation does not assert that infrastructure recovered. Collecting again reopens it.

`resume --json` returns the snapshotted service context, operation plan, evidence metadata, recorded notes, related same-environment sessions, knowledge paths, and next-check guidance. Read the artifacts, not just their descriptive summaries. Summaries of metric samples are not traffic-weighted aggregates and do not establish causality.

## History and incident promotion

```bash
reckon history --service checkout
reckon history --all-envs --service checkout --query timeout
reckon promote SESSION_ID --slug checkout-latency
```

History searches structured sessions and, when no environment filter is supplied, existing RCA Markdown. Legacy RCAs are marked as having an unverified environment; they are never silently mixed into environment-filtered results.

Promotion creates a preliminary snapshot at `incidents/<incident-date>-<slug>/` with the evidence, briefing, `RCA.md`, `alert.txt`, and `learnings.md`. It refuses to overwrite an existing incident. Complete `RCA.md` using the existing RCA template. Later session changes do not silently update that snapshot; `RCA.md` remains the authoritative incident narrative.

## Storage and boundaries

Sessions live in the org folder's `sessions/<id>/`. Each evidence record includes operation, source, connection, org, environment, requested/observed times, temporal scope, sanitized arguments/body, status, limits, artifact hashes, and completeness. Known credential values and common bearer/URL credentials are redacted from collected artifacts. Logs can still contain customer data; local evidence is not automatically safe to publish. No upload or notification is performed.

State files are written atomically with restrictive permissions, and an OS lock prevents concurrent session writers. A stopped collection leaves completed evidence intact; interrupted in-flight records are marked on the next collection. Current-state snapshots and recent Jenkins builds are labelled explicitly instead of being represented as historical window-filtered data.

State paths below the workspace reject symlinks/junctions. Evidence reads verify
artifact paths, byte counts and hashes before reuse, citation, reporting or
promotion. Missing or changed artifacts fail with an integrity error; restore the
original files from a trusted local backup or start a new session. Do not edit
checksums to bypass the error. Promotion validates its snapshot and refuses extra
linked files. These checks detect local corruption; they do not authenticate data
against someone who can rewrite both the artifact and its metadata.

Provider commands get a fresh environment: a limited set of operating-system,
locale, proxy and certificate variables plus the one connection's settings from
`~/.reckon/orgs/<org>/secrets/<connection>.env`. Credentials exported in your shell
are never passed on. Each connection has private HOME, cache, state and temporary
directories under `runtime/<connection>/` and its own CLI config under
`config/<connection>/`; global login caches are not used as a fallback. Secrets
files are parsed as literal `KEY=value` lines, never executed. Scoped paths are not
an operating-system filesystem sandbox.

The runner constrains commands routed through it. It cannot constrain every action of a general-purpose coding agent. Server-side read-only roles and existing database/query approval rules remain necessary. Source artifacts are untrusted data, not instructions to execute more commands.

## Verification and remaining work

Run `python3 -B -m unittest discover -s tests -p 'test_*.py' -v` plus the installer test. The suite exercises an offline cross-tool workflow, a non-CubeAPM metrics path, lifecycle commands, isolation, process limits, evidence references, redaction, and storage behavior.

This release implements the first deterministic workspace workflow. Automatic service discovery, persisted live-verification history, bounded parallel collection, calibrated agent evaluations, quantitative speed comparisons and live-server acceptance remain follow-up work. A synthetic walkthrough proves the plumbing, not an agent's diagnostic accuracy or a speed improvement.

The plan is in [PRODUCT-PLAN.md](PRODUCT-PLAN.md). [READINESS-REVIEW.md](READINESS-REVIEW.md)
records the September review of the pre-org layout.
