# Mode: investigate

Root-cause analysis for one specific incident: start at the symptom, walk down the causal chain a level at a time, quantify every hop with real data, and write up findings the team can act on.

> **Entered from [`../SKILL.md`](../SKILL.md).** That file carries what all modes share — the
> active-environment rule, the toolbelt, and how to read `infra-knowledge/`. Do not start an
> investigation without it; in particular, never query before you have stated which environment
> you are pointed at.

**Use this mode when there is a specific bad thing in a specific window** — an alert fired, a metric dropped, errors spiked, a queue backed up, a deploy is suspected. If nothing is known to be broken and you are sweeping for problems, use [`monitor.md`](monitor.md). If the question is about trends, capacity, cost, or performance shape rather than a fault, use [`analyze.md`](analyze.md).

## 1. Frame the question

Before touching any CLI, restate the question in a tight form:

- **Service** (exact metric label value — case-sensitive; check `services.md`)
- **Time window** (convert user-supplied local time to UTC; keep both)
- **Symptom** (Apdex drop to X, latency >Ys, error rate %, queue lag)
- **Severity / blast radius** if known

If the user says "18:51–19:21" without a timezone, default to the team's timezone (see `infra-knowledge/<env>/services.md` or ask), and work in UTC internally because every CLI uses UTC.

**Is the incident still ongoing?** Check whether the symptom metric is still degraded in the last 1–2 buckets. If it is, this is a live incident, not a post-mortem: surface mitigation candidates and the escalation path (`infra-knowledge/<env>/oncall.md`) up front, mark the RCA **preliminary**, and don't let a deep multi-level drill-down delay stabilization. The full cascade below still applies — but humans mitigating in parallel comes first. (This workspace is read-only, so you can investigate while others act.)

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

Expect values in [0, 1] in the normal case. Note CubeAPM weights penalty as `tolerated=1, frustrated=2` (see `infra-knowledge/_shared/metric-conventions.md`), so when frustrated calls dominate, `penalty` can exceed `calls` and this expression can read **negative** — that's a real "everything is frustrated" signal, not a bug. The sanity bound is `penalty ≤ 2×calls` (floor −1); a value below −1 means a label mismatch or counter reset, not a worse incident. Plot this over the window + 30 min buffer each side so you see the baseline and recovery. If Apdex doesn't exist, fall back to p99 latency via `cube_apm_latency_bucket` or an error-rate metric.

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

If you need request-level evidence (who called, what headers, which auth subject), use `cubeapm traces search` with the exact service + env + spanKind + time window. On some CubeAPM servers (see `infra-knowledge/<env>/server-quirks.md`) the trace-search response shape isn't Jaeger-standard and the CLI may fail; fall back to a raw curl with the saved session cookie.

Don't let missing trace data stop the RCA — it's common for bulk/infrequent endpoints not to be retained by tail sampling. Document it as a gap in the RCA and proceed.

### 3.8 Read the code path

Once the cascade has narrowed to one or two named services, **clone or check out the relevant repos and read the actual code path** before declaring the RCA done. Source answers questions that metrics often cannot:

- **Caller identity when APM instrumentation is gappy.** Many services issue HTTP calls from non-instrumented contexts (queue pollers, Kafka consumers, cron workers); the `span_kind="client"` topk will be empty and the caller is invisible. The code still names the caller in plain `client.post(...)` form.
- **The exact mechanism behind a slow DB op.** Whether an update is super-linear under concurrency because of a missing index, validators running on a deep schema, or the controller issuing several writes per request is visible in the model + repository + controller files in minutes; it is not visible in PromQL.
- **Whether a recent deploy touches the hot path.** `git log <deployed-branch> --until="<incident-time>"` plus `git show --stat <last-commit>` in the cloned repo identifies the actual diff that shipped before the incident. Grafana annotations may lack `deploy` tags and `service.version` is often `UNSET`; the git history isn't.

Practical recipe:

1. Find the repo (`gh repo view <org>/<repo>`, or the SSH remote pattern documented in `infra-knowledge/<env>/deploy-pipelines.md`).
2. Clone into the team's standard code dir if missing.
3. Discover the deployed branch from Jenkins (`jenkins build get <job> <num> -o json` → `params.branch`), then `git log <branch> --until="<incident-time>" --format="%h %cd %s" --date=iso -10` to find tip-of-release at incident time.
4. `git checkout <tip-sha>` so what you read is what was running.
5. Trace the request path: router → controller → repository → model → schema.
6. `git show --stat <tip-sha>` and `git log <tip>~5..<tip> -p -- <hot-path-files>` to see what the most recent merges changed on the hot path.

This step is cheap, often closes multiple unanswered questions at once, and pulls vague "the data is inconclusive" gaps into concrete code-locatable findings. **Do this before shipping an RCA that has more than one unresolved question.**

## 4. Cross-reference with Grafana and Jenkins

After you have a hypothesis, confirm or falsify it with the other CLIs:

- **Grafana annotations** near the incident window — any `deploy` tag? Any alert that fired? Note: `grafana annotation list --from/--to` uses **epoch milliseconds**, not RFC3339. Convert with `date -j -u -f "%Y-%m-%dT%H:%M:%SZ" "2026-04-19T13:00:00Z" +%s` and multiply by 1000. The `-u` is required — without it BSD `date` parses the timestamp in local time and the epoch is off by your UTC offset. (BSD/macOS form; on GNU/Linux use `date -u -d "2026-04-19T13:00:00Z" +%s`, or `python3 -c 'import datetime,sys;print(int(datetime.datetime.fromisoformat(sys.argv[1]).timestamp()))' 2026-04-19T13:00:00+00:00` on either.)
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
8. **Recognise the alert source before chasing the alert rule.** Not every notification corresponds to a Grafana alert rule — some come from CubeAPM-native alerting, some from the application itself, some from third parties (PagerDuty/Opsgenie composite policies). If the alert isn't in Grafana, `grafana alert rule list` and `grafana annotation list` are dead ends. Check `infra-knowledge/<env>/server-quirks.md` for the workspace's specific notification-template patterns (e.g., title prefixes that indicate CubeAPM-native vs Grafana-native), then go straight to the metric/log signal that the alert is built on.
9. **Don't anchor on the previous RCA when you re-investigate the same endpoint.** A fan-out orchestrator can hit a *different* dominant downstream each time it's stressed. Always recompute the §3.4 sub-span topk from scratch, even when the headline endpoint matches a prior incident. The corpus contains two RCAs five weeks apart on the *same* endpoint with entirely different root downstreams (one DB engine, then another) — treating the prior RCA as a prior on the downstream wastes queries and risks anchoring on the wrong fix.
10. **Don't assume `git log <default-branch>` shows what production is running — discover the deployed branch from Jenkins.** A service can deploy from `release`, `production`, a date-stamped branch, or whatever the team chose, and a local clone's default branch can be silently stale. Always pull the deployed branch from `jenkins build get <job> <num> -o json` → `params.branch` (see `infra-knowledge/<env>/deploy-pipelines.md`) before reading the diff. In one corpus incident the first RCA missed a deploy four hours before the incident because it read a 12-day-old default branch instead of the actual deployed branch.
11. **Inspect deploy *cause* and *params*, not just timestamp + result.** A `SUCCESS` build with a description like "Weekend override by <user>" and a manual cause is a load-bearing signal even when the diff is small — the operator who triggered the deploy may also be the operator who initiated the bulk action that caused the burst. Pull `description`, `causes[].shortDescription`, and `params[]` from `jenkins build get` for every build inside the incident's lookback window.
12. **For empty / negative findings, record the test result rather than dropping the question.** When a code-level hypothesis is tested against telemetry and falsified, write "Status: resolved — negative" in §7 with the evidence link. Don't quietly delete the question — it stops the next on-call re-running the same test and preserves why the corresponding root-cause headline was downgraded.
13. **Downgrade, don't delete.** If a root cause turns out not to be supported by data, keep its entry in §6 with severity downgraded and a one-line note linking to the negative evidence. The reasoning ("we thought X, here's why we no longer think X") is itself useful to future readers.
14. **Enumerate log datasources before assuming the obvious-named one covers everything.** A workspace often has more than one log surface with very different retention (e.g. `cubeapm logs` with short retention vs a Grafana Loki datasource retaining much longer) — for any RCA on a window older than the short surface's retention, those logs are *gone* and only the long one can answer. Run `grafana datasource list -o json | jq -r '.[] | "\(.type) \(.name)"'` once at investigation start and treat the result as the menu of forensic surfaces; check `infra-knowledge/<env>/server-quirks.md` for this workspace's actual retention numbers.
15. **For a producer endpoint with multiple branches (`if mode === X` / `if action === Y`), read the branching switch BEFORE assuming the default path fired.** A bulk endpoint with two branches is *not* doing the same thing under both. The actual branch comes from request bodies in logs or the corresponding DB audit table. Don't write a caller chain off the default branch and stop.
16. **Instant `metrics query` evaluates *now* — for a recovered or flapping incident, attribute with `--time <known-bad-ISO>`.** A `topk by (root_name)` / `sum by (span_name)` run after the symptom cleared reads the healthy steady-state and looks like "nothing wrong", and a live `sum/count` mean disagrees with the `query-range` history — an apparent contradiction that is really just recovery. Pin attribution to a bucket you've already confirmed was bad (the CLI supports `metrics query 'EXPR' --time <ISO>`). In `incidents/2026-06-25-conversational-ai-mongo-pool-starvation/`, the first instant attribution read a ~20 ms mean because an 89-minute choke had self-cleared ~10 minutes earlier; switching to `--time` at the peak resolved it.
17. **Uniform slowness across *every* collection/operation of one datastore = connection-pool checkout wait, not a slow query.** When a trivial indexed lookup and a heavy aggregate on the *same* client both jump to the same multi-second latency, the time is being spent waiting for a pooled connection — a few slow ops are holding the pool and starving everything else — so diagnose at the pool/holder, not the individual query. Distinguish it from a degraded datastore by checking *other* services on the same cluster (if they're fine, it's this client's pool) and the op-rate (flat rate + ~1000× per-op latency ⇒ not volume; the holder is expensive-per-call, not high-call-count). Confirm the holder from logs (the operation whose connections "timed out") and code. In `incidents/2026-06-25-conversational-ai-mongo-pool-starvation/`, every Mongo collection read 6–36 s (even a `findOne` at ~10 ops/s) while 24+ other services saw 3–16 ms — but see #18 before concluding the obvious service is the cause.
18. **When a shared datastore slows, profile its *other* tenants and separate supply-side from demand-side before naming a cause.** "Service X's DB latency exploded while 20+ other services are fine" does **not** mean X caused it — those others may be on *different* clusters. Find the services on the *same* cluster (co-moving latency at the same instant) and check their op-*rate*: if some client's rate **rose**, it's demand-side (find that caller); if **every** tenant's rate is **flat or collapses** while only latency rises, it's **supply-side — the datastore lost capacity** (failover, IOPS/burst-credit throttling, backup, index build, noisy neighbor), and the most-visible victim (the one with the heaviest/most-timeout-prone queries) is *not* the cause. Don't pin a server-side capacity event on the loudest victim. In the cited incident the first pass blamed a conversational report aggregate and declared the cluster "healthy for everyone else"; in fact a co-tenant doing 100→2 ops/s (97 % collapse, flat demand) proved a shared-mongod capacity event whose ultimate cause was unobservable from the available tooling — the aggregate was an amplifier, not the root. Corollary: if the datastore itself isn't monitored (no exporter series, no admin URI, no cloud metrics), say so and name exactly which metric source would close it — don't substitute the most-available victim's telemetry for the cause.
19. **Classify the incident and calibrate a confidence score — don't ship a free-text headline alone.** Assign exactly one `root_cause_category` from [references/root-cause-taxonomy.md](../references/root-cause-taxonomy.md) (narrowest that fits) and a `confidence` in [0,1]. The category is what makes the corpus searchable across incidents; the score forces honesty. Calibration: **≥0.85** only when the trigger is *proven* (a git diff on the hot path, a caller-rate series, a config change) and the causal chain is fully evidence-backed; **0.5–0.7** when the mechanism is understood but the trigger is inferred; **≤0.3** when you're naming a plausible cause without a confirming signal. A `confidence` above ~0.7 with any top-line claim still sitting in §0's *open/unvalidated* list is a contradiction — reconcile it. Then run the **self-consistency check** in the taxonomy file: if your prose's dominant subsystem disagrees with the category's group, either fix the category or (when the cause is legitimately upstream of the symptom) subtract ~0.15 and note the tension.

## 7. Output: the incident folder

Each investigation creates a folder at `incidents/<YYYY-MM-DD>-<slug>/` (UTC date the incident *started*; slug is `<service>-<symptom>` or `<service>-<root-cause>`, hyphenated). The convention is documented in [`../references/incidents-convention.md`](../references/incidents-convention.md) at the workspace root. The folder is gitignored by default so real service names, trace IDs, and timelines stay local; commit individually only after redaction.

Write the following files into the folder:

```
incidents/<YYYY-MM-DD>-<slug>/
├── RCA.md                    # required. The writeup. Follows ../references/rca-doc-template.md.
├── alert.txt                 # required when an alert kicked off the investigation. Verbatim notification text.
├── learnings.md              # required. Per-incident retrospective — what you got wrong and where each lesson now lives (skill / infra-knowledge / project memory).
└── evidence/                 # any raw output a future reader would need to verify a specific claim in RCA.md without re-running queries.
    ├── error-log-sample.json
    ├── error-message-counts.txt
    ├── <signal>-hits-1m.txt
    ├── <signal>-hits-30d.txt
    └── ...
```

`RCA.md` is the single source of truth. `evidence/` is retention insurance — logs and traces in CubeAPM rotate, but the snapshot saved at write time will not. `learnings.md` is the audit trail tying §9's routing decisions back to the incident that motivated them, so a future reader of `SKILL.md` §6 #N can find the originating incident and judge whether the rule still applies.

Use the writeup template in [references/rca-doc-template.md](../references/rca-doc-template.md) for `RCA.md`.

Required: a **header table**, a **structured diagnosis block (§0)**, and ten numbered sections.

- **Header table** — date, window (both local and UTC), peak impact number, services affected, triggered-by (one line), contributing latent issues, auto-recovery y/n, data gaps, **root-cause category**, **confidence**.
- **§0 Diagnosis (structured)** — a machine-greppable block carrying `root_cause_category` (exactly one leaf name from [references/root-cause-taxonomy.md](../references/root-cause-taxonomy.md)), `incident_status`, a `confidence` score (0.0–1.0), and an explicit split of **validated claims** (each cites a §4 subsection) vs **open/unvalidated claims** (hypotheses, not conclusions). This is what makes the corpus classifiable — a future on-call greps `root_cause_category:` across `incidents/` to find every prior instance of a failure mode. Pick the **narrowest** category the evidence supports; run the **self-consistency check** (below) before you commit the score.
1. **Incident summary** — 1–2 paragraphs a senior engineer could paste into a postmortem doc.
2. **Timeline** — bucketed metric values showing the drop and recovery. 2-minute buckets for ~30 min windows.
3. **Causal chain diagram** (ASCII arrows) — symptom → service → endpoint → downstream → root.
4. **Evidence** — one subsection per cascade level with the actual numbers and queries that produced them.
5. **Why recovery was automatic (or not).**
6. **Root causes, ranked by leverage** — which fix prevents the most recurrence?
7. **Unanswered questions & data gaps** — be explicit about what you couldn't close and what would close it.
8. **Recommendations** — split into must-fix / should-fix / nice-to-have.
9. **Appendix of queries used** — so the next on-call can re-run them.
10. **Reproducibility** — how to re-run the whole investigation from scratch, including any login prerequisites.

Optimize the document for: (a) the team that owns the service reads it once and knows what to do; (b) a future on-call searching for `apdex drop` finds it and learns the pattern.

## 8. What not to do

- Do not concatenate unrelated screenshots or data dumps into the RCA — each number should be motivated by a question.
- Do not speculate beyond the data. Before each sentence in the RCA, ask: *is this an observation, a derivation from observation, or a guess?* Guesses go in §7 (Unanswered Questions), not in §1 (Summary), §3 (Causal Chain), §5 (Why Recovery), or §6 (Root Causes). Phrases that should trigger you to re-check: *"likely"*, *"most likely"*, *"consistent with"*, *"could be"*, *"plausible candidates"*, *"suggests that"* — each is a tell that you're filling in for missing data.
- Do not skip the unanswered-questions section. Leaving gaps implicit creates the illusion of a complete story and prevents follow-ups.
- Do not recommend fixes without ranking them. "We should also look at X" is noise; "X is the highest-leverage fix because Y" is signal.
- Do not use `cubeapm traces services` as a service inventory — it fails against some servers and is a poor list regardless. Use `cubeapm metrics label-values service` and the `infra-knowledge/<env>/services.md` file.
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
| Generic RCA discipline (any service, any stack)    | the `reckon` skill: SKILL.md §6 / §8 / cascade-playbook |
| Workspace-specific quirk (CLI behavior, server bug, label gotcha) | `infra-knowledge/<env>/server-quirks.md` (or `metric-conventions.md` / `known-issues.md`) |
| User preference / writing style / approval pattern | project memory as `feedback` type           |
| New service relationship, slow query, fan-out fact | `infra-knowledge/<env>/services.md` / `known-issues.md` |

**Redaction rule — read before editing SKILL.md.** This skill ships as a generic product (see §0: "It is generic on purpose"). Anything routed *into the skill body* must be tenant-agnostic: strip real service names, hostnames, SSH remotes, JWT/account identifiers, and dated incident specifics. Phrase the rule generically and cite the originating incident by folder path (`incidents/<date>-<slug>/`) rather than inlining the names — the concrete evidence lives in that folder's `learnings.md`. Team-specific facts belong in `infra-knowledge/`, never in the skill. (SKILL.md is one tracked file at `skills/reckon/`; the loaded `.claude/skills/reckon` is a symlink to it, so an edit here *is* the product edit — there is no second copy to sync.)

Two-line minimum per learning: state the rule, then **why** (cite the actual numbers / timestamps / phrases from this incident as evidence), so a future reader knows whether it still applies.

If nothing was learned — say so explicitly in your wrap-up message rather than skipping the step. Two clean RCAs in a row is a real signal; silent skipping is not.

## Reference files

- [references/cascade-playbook.md](../references/cascade-playbook.md) — per-level checklists with exact commands
- [references/query-recipes.md](../references/query-recipes.md) — PromQL and LogsQL snippets indexed by "what are you trying to find out"
- [references/rca-doc-template.md](../references/rca-doc-template.md) — the RCA write-up skeleton
- [references/root-cause-taxonomy.md](../references/root-cause-taxonomy.md) — the controlled `root_cause_category` vocabulary + the category↔prose self-consistency check
