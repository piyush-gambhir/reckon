---
name: rca-assist
description: Use whenever the user asks to investigate a production incident, trace why a service is slow or erroring, understand an alert that fired, perform root-cause analysis, or ask "why did X drop / spike / fail". Triggers on Apdex drops, latency regressions, error-rate spikes, queue lag, service-down alerts, deploy suspicions, or any narrative like "the X service degraded between 13:00 and 13:30 UTC — what happened?". This skill coordinates the sibling CLIs (cubeapm, grafana, jenkins) to run a cascade-style investigation from symptom down to root cause and produces a written RCA. Use it even when the user doesn't say "RCA" explicitly — any narrow window + service + bad number is an RCA.
---

# rca-assist

This skill turns the agent into a disciplined on-call engineer. It captures the workflow a practiced SRE uses: start at the symptom, walk down the causal chain one level at a time, quantify each hop with real data, and write up findings in a form the team can act on.

It is generic on purpose. The *methodology* applies to any service; the *facts* about your environment live in `infra-knowledge/` (services, metric label conventions, known-slow queries, server quirks, deploy pipelines). Always check that folder first — it saves the agent from re-discovering what the team already knows.

The CLIs this skill expects to be available as sibling skills:

- `cubeapm` — traces, metrics (PromQL), logs (LogsQL). Primary source of performance signals.
- `grafana` — dashboards, alerts, annotations. Source of deploy markers and alert history.
- `jenkins` — builds, pipelines. Confirms whether a deploy preceded the incident.

If any CLI isn't logged in, don't block — note the gap, continue with what's available, and include the gap in the RCA's "unanswered questions" section.

## 0. Before anything: read `infra-knowledge/`

Look for `infra-knowledge/` at the workspace root (typically `./infra-knowledge/` from where the agent is running). If present, read at least:

- `services.md` — canonical service names and what each does
- `metric-conventions.md` — label names, time-of-day gotchas, which metrics to query
- `server-quirks.md` — reverse proxy or API quirks the CLIs have to work around
- `known-issues.md` — queries or endpoints the team already knows are slow/fragile
- `oncall.md` — who to escalate to

These files save you from rediscovering facts and prevent wrong conclusions (e.g., blaming "new" slowness on an incident when it's actually been slow all day). Do not fabricate content when files are missing — just note what's missing and move on.

## 1. Frame the question

Before touching any CLI, restate the question in a tight form:

- **Service** (exact metric label value — case-sensitive; check `services.md`)
- **Time window** (convert user-supplied local time to UTC; keep both)
- **Symptom** (Apdex drop to X, latency >Ys, error rate %, queue lag)
- **Severity / blast radius** if known

If the user says "18:51–19:21" without a timezone, default to the team's timezone (see `infra-knowledge/services.md` or ask), and work in UTC internally because every CLI uses UTC.

## 2. The cascade pattern

RCA is a drill-down, not a shotgun. At each level you answer two questions: **where did the time/errors go** and **what fed that level with load or bad inputs**. Keep drilling until you reach a level whose inputs do not change during the window — that is the root.

```
(symptom)
  │
  ▼
service-level signal                        ← alert / Apdex / p99
  │      "which endpoint(s) caused this?"
  ▼
endpoint-level signal                       ← request rate, latency, error rate
  │      "where did time inside this endpoint go?"
  ▼
sub-span breakdown                          ← DB ops, downstream HTTP, queue waits
  │      "which downstream owns this?"
  ▼
downstream service                          ← (recurse: apply the same pattern here)
  │      "was this downstream already slow, or did something push it?"
  ▼
downstream's inputs                         ← DB query pattern, pool saturation, upstream traffic shape
  │      "what changed to cause the input shift?"
  ▼
trigger                                     ← deploy, cron, bulk-admin action, caller spike
```

Every step produces either a metric-over-time plot (2-min buckets are usually right for 30-min windows) or an aggregate attribution (topk by root_name or span_name).

The key discipline: **distinguish per-call latency change from call-volume change**. Many cascades look like "service slowed down" when the truth is "service's steady-state was always slow, then volume jumped 10× and the connection pool saturated." The fix for those two situations is completely different. See §6.

## 3. Step-by-step: cubeapm playbook

Assume metrics are the primary signal (richest labels, longest retention). Use traces for one concrete request, logs for exceptions and human-readable detail.

### 3.1 Confirm the symptom

For Apdex-style alerts on a service:

```
(sum(rate(cube_apm_apdex_calls_total{service="<SVC>"}[5m]))
 - sum(rate(cube_apm_apdex_penalty_total{service="<SVC>"}[5m])))
 / sum(rate(cube_apm_apdex_calls_total{service="<SVC>"}[5m]))
```

Expect values in [0, 1]. Plot this over the window + 30 min buffer each side so you see the baseline and recovery. If Apdex doesn't exist, fall back to p99 latency via `cube_apm_latency_bucket` or an error-rate metric.

### 3.2 Attribute to an endpoint

Which `root_name` soaked up the Apdex penalty (or the errors, or the slow calls)?

```
topk(15, sum by (root_name) (
  rate(cube_apm_apdex_penalty_total{service="<SVC>"}[10m])
))
```

You almost always see one or two root_names responsible for >80%. Focus on those.

### 3.3 Compare per-call latency vs call-volume for the guilty endpoint

This is the hinge step — it determines whether the endpoint itself got slow, or just got busy.

**Per-call server-span latency over time:**
```
sum(rate(cube_apm_latency_sum{service="<SVC>",root_name="<ROOT>",span_kind="server"}[2m]))
/ sum(rate(cube_apm_latency_count{service="<SVC>",root_name="<ROOT>",span_kind="server"}[2m]))
```

**Call rate over time:**
```
sum(rate(cube_apm_latency_count{service="<SVC>",root_name="<ROOT>",span_kind="server"}[2m]))
```

Always plot both. If per-call latency changed → downstream issue or local slowdown. If call rate changed → find the caller.

### 3.4 Break latency down into sub-spans

For a server-side endpoint, the wall-clock time is divided among sub-spans: DB queries, outbound HTTP calls, internal work. Rank them by total time contribution:

```
topk(15, sum by (span_name, group_name) (
  rate(cube_apm_latency_sum{service="<SVC>",root_name=~"<ROOT_REGEX>",span_kind!="server"}[10m])
))
```

Output is in "ms of span time per second of wall clock" — a good proxy for where time went during the window. The top 1–3 rows point at the next service or the next query to drill into.

### 3.5 Drill into the suspected downstream service

If a downstream HTTP span is at the top, recurse: treat that downstream as the service and re-run §3.1 through §3.4.

If a DB span is at the top, check: did **per-query** latency change during the window, or is it stable and just called more often? A 24-hour time-series on `rate(sum)/rate(count)` tells you immediately. Stable-but-slow DB queries are usually latent bugs; the incident just exposed them.

### 3.6 Find the caller / trigger

Once you reach a level whose inputs changed (traffic spike, new workload, etc.), identify who pushed them:

- **For HTTP client spans calling the offender:** `sum by (service) (rate(cube_apm_latency_count{group_name="HTTP <host>",span_kind="client"}[2m]))` — aggregate outbound call rate by service. Services with a big ratio (peak/baseline) are suspects.
- **For scheduled jobs:** `CADENCE-*` services' `OtherTransaction/Function/...` root_names. If their rate didn't change, scheduler isn't the trigger.
- **For admin-console actions:** look at `CONSOLE-BACKEND` (or whatever your admin panel is) root_names that match keywords in the trigger endpoint (e.g., GETs to `/console/v1/<feature>/*` just before the incident).
- **For one-off bulk endpoints:** look for a single server call on a `bulk/*` or `internal/*` endpoint right at incident start.

### 3.7 Close the loop with a concrete trace

If you need request-level evidence (who called, what headers, which auth subject), use `cubeapm traces search` with the exact service + env + spanKind + time window. On some CubeAPM servers (see `infra-knowledge/server-quirks.md`) the trace-search response shape isn't Jaeger-standard and the CLI may fail; fall back to a raw curl with the saved session cookie.

Don't let missing trace data stop the RCA — it's common for bulk/infrequent endpoints not to be retained by tail sampling. Document it as a gap in the RCA and proceed.

## 4. Cross-reference with Grafana and Jenkins

After you have a hypothesis, confirm or falsify it with the other CLIs:

- **Grafana annotations** near the incident window — any `deploy` tag? Any alert that fired? Note: `grafana annotation list --from/--to` uses **epoch milliseconds**, not RFC3339. Convert with `date -j -f "%Y-%m-%dT%H:%M:%SZ" "2026-04-19T13:00:00Z" +%s` and multiply by 1000.
- **Grafana alert rules** — does an alert for the suspected service exist? What was its state?
- **Jenkins builds** on the suspected service's deploy job during the window. Confirm whether a release shipped, and what commit range it covered.

If CubeAPM has native alerting, Grafana annotations will be empty for those alerts. Don't conclude "no alert fired" from an empty Grafana annotations response — check the actual alerting surface.

## 5. When to stop

Stop the cascade when **one of**:

- You've identified the trigger (a deploy, a caller spike, a bulk admin action, a cron change, an upstream dependency outage) — the chain explains itself from there.
- You've reached a platform level (DB, network, cloud provider) you can't introspect with these CLIs — name it and stop.
- You've gone three levels without the picture getting clearer — back up and question the framing, the time window, or the service label.

A good RCA finishes with a single sentence a reader could restate: *"X happened because Y triggered Z, which pushed load into W, which was already holding a latent issue."*

## 6. Pitfalls to check before concluding

1. **Did per-call latency actually change, or just call volume?** A slow endpoint might be slow all day. Look at a 24 h baseline on the suspected metric (`avg over 15m` step) before calling out "new slowness".
2. **Are you looking at the right service name?** Labels can be inconsistent (e.g., `MEDIA-SERVICE` vs `Media-Service`). Cross-check with `cubeapm metrics label-values service`.
3. **Is the trace sampler hiding the real root span?** Low-volume endpoints often aren't retained. Don't conclude "no such call" from an empty trace search.
4. **Is the alert's tolerance threshold realistic?** Apdex goes to 0.2 when every request is frustrated — check what absolute latency number that corresponds to for your endpoint's threshold.
5. **Distinguish cause from effect.** Every downstream spike looks "caused" by the next level down, but the real cause might be a lateral caller change. Always do step 3.6 before declaring a root.
6. **Don't trust fresh log buckets (~last 5 min) for recovery determination.** VictoriaLogs/CubeAPM ingestion can lag several minutes. A bucket that reads 0 right now may backfill to 20+ five minutes from now. Three concrete defenses, in order of preference:
   - Cross-check with the equivalent **metric** (counter histograms ingest faster than logs and don't backfill the same way). If `cube_apm_latency_count{http_code=~"5.."}` shows continuing errors, trust that over a low log-hits bucket.
   - **Re-query** the same `logs hits` once after ~5 minutes and confirm the bucket counts didn't move. If they did, treat the earlier read as provisional.
   - Tag any near-realtime recovery claim in the RCA with the read time (e.g., *"last error log at 18:30 UTC, observed at 18:33 UTC"*) so a reader knows to discount it.
   This is the single most common way an RCA gets the recovery time wrong; budget the extra 5 min before writing the document.
7. **For `/internal/`, `/private/`, or service-to-service endpoints, identify the caller service before writing the RCA.** A failing endpoint with `userAgent=node` and an RFC1918 `context.ip` has *some* internal owner; finding it (or proving it's not APM-instrumented) is part of the RCA, not a follow-up. See cascade playbook Level 6 "caller discovery for internal endpoints".
8. **Recognise the alert source before chasing the alert rule.** Not every notification corresponds to a Grafana alert rule — some come from CubeAPM-native alerting, some from the application itself, some from third parties (PagerDuty/Opsgenie composite policies). If the alert isn't in Grafana, `grafana alert rule list` and `grafana annotation list` are dead ends. Check `infra-knowledge/server-quirks.md` for the workspace's specific notification-template patterns (e.g., title prefixes that indicate CubeAPM-native vs Grafana-native), then go straight to the metric/log signal that the alert is built on.

## 7. Output: the RCA document

Write the finished RCA to a file at the workspace root, named `RCA-<YYYY-MM-DD>-<slug>.md`. Use the template in [references/rca-doc-template.md](references/rca-doc-template.md).

Required sections:

1. **Header table** — date, window (both local and UTC), peak impact number, services affected, triggered-by (one line), contributing latent issues, auto-recovery y/n, data gaps.
2. **Incident summary** — 1–2 paragraphs a senior engineer could paste into a postmortem doc.
3. **Timeline** — bucketed metric values showing the drop and recovery. 2-minute buckets for ~30 min windows.
4. **Causal chain diagram** (ASCII arrows) — symptom → service → endpoint → downstream → root.
5. **Evidence** — one subsection per cascade level with the actual numbers and queries that produced them.
6. **Why recovery was automatic (or not).**
7. **Root causes, ranked by leverage** — which fix prevents the most recurrence?
8. **Unanswered questions & data gaps** — be explicit about what you couldn't close and what would close it.
9. **Recommendations** — split into must-fix / should-fix / nice-to-have.
10. **Appendix of queries used** — so the next on-call can re-run them.

Optimize the document for: (a) the team that owns the service reads it once and knows what to do; (b) a future on-call searching for `apdex drop` finds it and learns the pattern.

## 8. What not to do

- Do not concatenate unrelated screenshots or data dumps into the RCA — each number should be motivated by a question.
- Do not speculate beyond the data. Before each sentence in the RCA, ask: *is this an observation, a derivation from observation, or a guess?* Guesses go in §7 (Unanswered Questions), not in §1 (Summary), §3 (Causal Chain), §5 (Why Recovery), or §6 (Root Causes). Phrases that should trigger you to re-check: *"likely"*, *"most likely"*, *"consistent with"*, *"could be"*, *"plausible candidates"*, *"suggests that"* — each is a tell that you're filling in for missing data.
- Do not skip the unanswered-questions section. Leaving gaps implicit creates the illusion of a complete story and prevents follow-ups.
- Do not recommend fixes without ranking them. "We should also look at X" is noise; "X is the highest-leverage fix because Y" is signal.
- Do not use `cubeapm traces services` as a service inventory — it fails against some servers and is a poor list regardless. Use `cubeapm metrics label-values service` and the `infra-knowledge/services.md` file.
- Do not declare recovery from a fresh log bucket alone (see Pitfall #6). Cross-check with metrics, or re-query after one ingestion window, before writing the recovery time into the RCA header.

## 9. Post-incident self-review (mandatory before closing the loop)

After the RCA file is written and the user has acknowledged it, do a short retrospective on the investigation itself. This is not optional — every incident should produce learnings. Skip it and the next on-call repeats the same mistakes.

Ask, in order:

1. **Did I correct any conclusion mid-investigation?** (Recovery time, root cause, blast radius.) If yes, *why* did I get it wrong the first time? Was it bad data, a missed cross-check, or a discipline lapse (speculation, premature closure)? Each correction is a candidate learning.
2. **What took longer than it should have?** Wasted round-trips on the wrong tool, jq parse errors, dead-end queries against unsupported endpoints, hunting for things `infra-knowledge/` already documents.
3. **What did the user have to ask for that I should have produced unprompted?** Caller identification, alert state confirmation, deploy correlation, etc.
4. **What workspace knowledge would have saved me time?** Anything that's not in `infra-knowledge/` yet but should be.

Then route each learning to the right home:

| Kind of learning                                   | Goes in                                    |
|----------------------------------------------------|--------------------------------------------|
| Generic RCA discipline (any service, any stack)    | the `rca-assist` skill: SKILL.md §6 / §8 / cascade-playbook |
| Workspace-specific quirk (CLI behavior, server bug, label gotcha) | `infra-knowledge/server-quirks.md` (or `metric-conventions.md` / `known-issues.md`) |
| User preference / writing style / approval pattern | project memory as `feedback` type           |
| New service relationship, slow query, fan-out fact | `infra-knowledge/services.md` / `known-issues.md` |

Two-line minimum per learning: state the rule, then **why** (cite the actual numbers / timestamps / phrases from this incident as evidence), so a future reader knows whether it still applies.

If nothing was learned — say so explicitly in your wrap-up message rather than skipping the step. Two clean RCAs in a row is a real signal; silent skipping is not.

## Reference files

- [references/cascade-playbook.md](references/cascade-playbook.md) — per-level checklists with exact commands
- [references/query-recipes.md](references/query-recipes.md) — PromQL and LogsQL snippets indexed by "what are you trying to find out"
- [references/rca-doc-template.md](references/rca-doc-template.md) — the RCA write-up skeleton
