---
name: reckon
description: Use whenever the user asks anything about the state, health, history, or behaviour of their infrastructure. Three kinds of request all belong here. (1) INVESTIGATE — a specific incident: "why did X drop / spike / fail", an alert that fired, an Apdex or latency regression, an error-rate spike, queue lag, a service-down page, a deploy suspicion, or any narrative like "the X service degraded between 13:00 and 13:30 UTC — what happened?". Use it even when the user never says "RCA": any narrow window + service + bad number is an investigation. (2) MONITOR — no known fault, sweeping for problems: "is anything wrong right now", "how does prod look", morning health checks, pre/post-deploy verification, drift or anomaly hunting. (3) ANALYZE — no fault at all, a question about shape over time: capacity headroom, cost drivers, slow-query or endpoint profiling, traffic growth, "which service should we optimize first". This skill coordinates read-only observability, CI/CD, cloud, queue, and database CLIs across production, staging, and UAT.
---

# reckon

reckon turns the agent into a disciplined SRE working against real infrastructure. This file is the **router and the shared spine**: the environment rule, the toolbelt, how to read the knowledge base, and the discipline that applies to every mode. Read it first, then enter the mode that fits the request.

It is generic on purpose, in two directions. **Tenant-agnostic:** the *methodology* lives here; the *facts* about a given environment live in `infra-knowledge/` (services, metric label conventions, known-slow queries, server quirks, deploy pipelines). Always check that folder first — it saves the agent from re-discovering what the team already knows. **Runtime-agnostic:** this skill is the shared core of both reckon editions (see [EDITIONS.md](../../EDITIONS.md)), so never write a rule here that assumes a shell, direnv, or a human standing by to approve the next query. Describe *what to check and why*. Where a step genuinely depends on human approval, say so explicitly — the hosted edition has no such human and needs to know which guarantees it loses.

---

## 0. Environment: state it before you query anything

This workspace can point at **production, staging, or UAT**, and the credentials, service inventory, and quirks are different for each. Every environment is loaded through one variable, `RECKON_ENV`, which pins a matching credential set and config directory.

The failure this rule exists to prevent is querying production while believing you are on staging. It is silent, it is easy, and no tool will catch it for you.

**Before the first query of any task:**

1. **Resolve the active environment.** Read `RECKON_ENV`. If it is unset, empty, or you cannot determine it with certainty, **stop and ask** — do not assume, and do not guess from context like service names or hostnames.
2. **State it back to the user, explicitly**, in your first substantive message: *"Working against **production**."* Repeat it whenever it changes mid-session.
3. **Never mix environments inside one investigation, sweep, or analysis.** A comparison across environments (e.g. "staging is fine, prod isn't") is legitimate, but every number must be labelled with the environment it came from, and you must switch deliberately, announcing the switch.
4. **Record it in the output.** RCAs carry an `Environment` header row and an `incident_status` block; health reports and analyses must name the environment in their first line.

**Production is the strict one.** The read-only posture is *identical* in all three environments — the environment split is about *which* infrastructure you touch, never about relaxing safety — but the blast radius of a mistake is not. In production, prefer the cheaper signal, bound every query harder, and escalate to a human sooner.

---

## The toolbelt

The spine of every mode:

- `cubeapm` — traces, metrics (PromQL), logs (LogsQL). Primary source of performance signals.
- `grafana` — dashboards, alerts, annotations. Source of deploy markers and alert history.
- `jenkins` — builds, pipelines. Confirms whether a deploy preceded a change in behaviour.

Extended surfaces, reached for at specific steps — see `CLAUDE.md` for exact commands and the full safety contracts:

- **`gh`** — merged PRs, GitHub Actions runs, releases. Deploy correlation for services that ship via Actions, and to read the *intent* behind a change.
- **`aws`** — CloudWatch / ALB / SQS / ECS. Fallback when APM signals are thin (`host.name=UNSET`, uninstrumented callers, or a documented log-ingestion lag).
- **`rpk` / `kcat`** — Kafka consumer-group lag and topic tails. Resolve service→consumer-group via `infra-knowledge/_shared/service-name-mapping.md`. Read-only by usage: never produce, and never consume with a group id (`-G`/`-g`) — joining a production consumer group rebalances it. Kafka safety contract: `CLAUDE.md` §5b.
- **`kubectl`** — pod/deploy state, events, rollout history (read-only by usage: never `apply`/`delete`/`scale`/`exec`). Use when a service emits zero telemetry — the first question is "was anything running?".
- **`redis-cli`** — Redis diagnostics (`INFO`, `SLOWLOG GET`, `LATENCY`; never `FLUSH*`/`SET`/`DEL`).
- **`mongosh` / `psql` / `mysql` / `clickhouse client`** — read-only DB shells. Only after the work has narrowed to a specific table/collection/query. **Re-read the Database safety contract in `CLAUDE.md` first: read-only role, `EXPLAIN` before any non-trivial SELECT, `LIMIT` always, and every call prompts for approval.**
- **`es`** (optional) — Elasticsearch/ELK log stores for services that don't ship logs to the APM. Client-side read-only via `ES_READ_ONLY=true`.

**This list is what reckon *can* reach, not what is reachable right now.** Before the first query, obtain a **capability digest** for the active environment — which integrations actually have a working binary and credentials, and which do not — and trust it over this list. Discovering a missing credential midway through a cascade wastes every query that preceded it. How the digest is produced is runtime-specific; the workspace edition provides a command for it (see `CLAUDE.md`).

If a CLI isn't logged in, don't block — note the gap, continue with what's available, and carry the gap into the output as an explicit limitation.

---

## Before anything: read `infra-knowledge/`

The knowledge base is **per-environment**, with a shared layer underneath:

```
infra-knowledge/
  _shared/     facts true in every environment
  <env>/       facts specific to the active RECKON_ENV
```

Read `_shared/` first, then overlay the active environment's directory — **a value in `<env>/` always wins over `_shared/`.**

| Layer | Files | What they hold |
|---|---|---|
| `_shared/` | `metric-conventions.md` | Label names, time-of-day gotchas, which metrics to query |
| | `service-name-mapping.md` | Cross-tool lookup: APM label ↔ logs `service.name` ↔ Grafana folder ↔ Kafka consumer group ↔ Jenkins job. Read before any name resolution. |
| | `bulk-endpoints.md` | Endpoints that generate sustained downstream load |
| `<env>/` | `services.md` | Canonical service names and what each does |
| | `known-issues.md` | Queries/endpoints already known to be slow or fragile |
| | `server-quirks.md` | Reverse-proxy or API quirks the CLIs work around |
| | `deploy-pipelines.md` | Per-service deploy job + deployed branch |
| | `oncall.md` | Who to escalate to |

The authoritative description of each file is [`references/infra-knowledge-guide.md`](references/infra-knowledge-guide.md). Read it and skim every file it names.

These files prevent wrong conclusions — for example blaming "new" slowness on an incident when it has been slow all day. **Do not fabricate content when files are missing.** Note the gap and move on; an empty knowledge base is a normal state for a fresh clone, not an error.

---

## Choosing a mode

| Mode | Enter when | Produces |
|---|---|---|
| **[investigate](modes/investigate.md)** | A specific bad thing in a specific window — alert fired, metric dropped, errors spiked, queue backed up, deploy suspected | An RCA at `incidents/<YYYY-MM-DD>-<slug>/` |
| **[monitor](modes/monitor.md)** | Nothing known to be broken; you are sweeping — health check, "does anything look wrong", pre/post-deploy verification, drift hunting | A health report; escalates into `investigate` on a real finding |
| **[analyze](modes/analyze.md)** | No fault at all — a question about shape over time: capacity, cost, performance profiling, growth | An analysis writeup |

Pick by **what's known**, not by what the user called it: a request phrased as "just check something" that names a bad number in a window is an *investigation*. When a sweep in `monitor` surfaces a genuine problem, hand off to `investigate` rather than half-doing an RCA inline. When an investigation's root cause turns out to be "we're simply out of headroom", finish the RCA, then hand the capacity question to `analyze`.

---

## Shared discipline (every mode)

- **Always `-o json`** when output will be parsed.
- **Start broad, then narrow.** List services before drilling into one.
- **Correlate timestamps**, and keep UTC internally — every CLI uses it. Convert user-supplied local times and keep both.
- **Check multiple signals.** Never conclude from traces alone or metrics alone.
- **Separate observation from inference.** Before each written sentence, ask: *is this an observation, a derivation from observation, or a guess?* Guesses are labelled as such and never stated as findings. Words that should make you re-check: *likely*, *most likely*, *consistent with*, *could be*, *plausible*, *suggests that*.
- **Be explicit about uncertainty and gaps.** What you could not check, and why, is part of the output — never an omission.
- **Bound every query.** `LIMIT` on selects, explicit time windows, `topk` over unbounded lists. This workspace observes production; a runaway query is a real outage source.
- **Never mutate anything.** No writes, no `kubectl apply/delete/scale`, no Jenkins builds, no Kafka produce, no consumer-group joins. reckon observes; humans mitigate.
- **Persist findings where they belong**, never at the repo root — see [`references/incidents-convention.md`](references/incidents-convention.md).

## Reference files

- [modes/investigate.md](modes/investigate.md) · [modes/monitor.md](modes/monitor.md) · [modes/analyze.md](modes/analyze.md) — the three modes
- [references/cascade-playbook.md](references/cascade-playbook.md) — per-level checklists with exact commands
- [references/query-recipes.md](references/query-recipes.md) — PromQL and LogsQL snippets indexed by "what are you trying to find out"
- [references/rca-doc-template.md](references/rca-doc-template.md) — the RCA write-up skeleton
- [references/root-cause-taxonomy.md](references/root-cause-taxonomy.md) — the controlled `root_cause_category` vocabulary + the category↔prose self-consistency check
- [references/infra-knowledge-guide.md](references/infra-knowledge-guide.md) — what each knowledge file holds
- [references/incidents-convention.md](references/incidents-convention.md) — the incident folder layout
- [templates/infra-knowledge/](templates/infra-knowledge/) — seed templates (`_shared/` + `env/`) used by `scripts/setup.sh`
