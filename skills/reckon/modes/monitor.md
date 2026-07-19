# Mode: monitor

Sweeping for problems when nothing is known to be broken — a health check across an environment, a pre/post-deploy verification, a morning "does anything look wrong", drift and anomaly hunting.

> **Entered from [`../SKILL.md`](../SKILL.md).** That file carries the active-environment rule,
> the toolbelt, and how to read `infra-knowledge/`. State which environment you are sweeping
> before the first query — a sweep silently run against the wrong environment produces a
> confidently wrong "all clear".

**Use this mode when the answer isn't known to be "something is wrong."** If a specific metric in a specific window is already known bad, that's [`investigate.md`](investigate.md). If the question is about shape over time rather than current health, that's [`analyze.md`](analyze.md).

## 1. Set the scope before sweeping

A sweep without a declared scope silently becomes "whatever I happened to check." State all four up front:

- **Environment** — from `RECKON_ENV`.
- **Breadth** — whole environment · one service and its dependencies · one subsystem (queues, datastores, cluster).
- **Window** — the period being judged. Default `--last 1h` for "right now"; `--last 24h` when asked "has anything been wrong today".
- **Trigger** — routine check · post-deploy verification · a hunch · a user report with no numbers yet.

Post-deploy verification is a distinct shape: the window is *since the deploy*, the comparison is *the equivalent window before it*, and the services in scope are the deployed one plus its immediate downstreams.

## 2. The baseline discipline — the thing that makes a sweep meaningful

**"Wrong" is only ever relative to normal.** A 4% error rate is catastrophic for one service and the steady state for another. Before flagging anything, pull the same metric for a **baseline window** and compare:

- **Same time yesterday** — controls for time-of-day, which dominates most infra metrics.
- **Same time last week** — controls for day-of-week (Monday ≠ Saturday traffic).
- **The last few hours** — catches a step change that started recently.

A finding without a baseline number next to it is not a finding; it is a reading. Write both.

## 3. The standard sweep

Work top-down. Stop and escalate the moment something is clearly broken — don't finish the checklist first.

1. **Alerts.** Firing rules and, critically, **active silences** — a silenced alert is a hidden problem, not an absent one. Check the native alerting surface too; not every alert is a Grafana rule (see `investigate.md` pitfall #8).
2. **Signal presence.** Which services are emitting at all. **Do this before error rates** — see the no-data trap below.
3. **Error rates.** 5xx and error-status traces per service, against baseline.
4. **Latency.** p99 / Apdex per service, against baseline.
5. **Saturation.** CPU, memory, connection pools, thread pools — the things that produce cliff-edge failure rather than gradual slowdown.
6. **Queue lag.** Consumer-group lag and its *direction*. A large but flat lag is a backlog; a small but climbing lag is an incident forming.
7. **Workload health.** Pod restarts, OOMKills, CrashLoopBackOff, Pending, and any rollout that hasn't completed.
8. **Datastores.** Connection counts against limits, replication lag, slow-query counts, disk/IOPS headroom.
9. **Deploy freshness.** What shipped recently — it contextualises everything above and is the first correlation you'll want if a finding turns real.

## 4. Triage what you find

For each finding, record four things — this is what separates a useful sweep from a wall of numbers:

| Field | Why |
|---|---|
| **Value + baseline** | The deviation, not the reading |
| **Is it new?** | New deviation vs. a long-standing condition. Check `infra-knowledge/<env>/known-issues.md` before flagging anything as new — a "finding" the team documented months ago is noise. |
| **Direction** | Improving, stable, or worsening. Worsening outranks larger-but-stable. |
| **Blast radius** | One service, or a shared dependency with tenants behind it |

Rank by *worsening × blast radius*, not by which number looks worst.

## 5. Pitfalls specific to sweeping

1. **No data is not health — it is the most dangerous reading on the board.** A service emitting zero metrics and zero logs shows a 0% error rate and a clean latency chart. Every green panel for a dead service is green *because* it is dead. Always confirm signal presence (step 2) before believing any per-service health number, and treat "this service went quiet" as a P1-shaped finding until proven otherwise.
2. **A silence is a finding.** Check what's silenced and why, and whether the silence outlived the incident that justified it.
3. **Don't trust the freshest buckets.** Log ingestion can lag minutes and backfills. A last-5-minutes bucket reading zero may fill in later. Cross-check with a metric, or re-query after one ingestion window, before declaring "all clear". Tag any real-time claim with the read time.
4. **Sweeping is not investigating.** When you find something real, stop sweeping, note it, and enter [`investigate.md`](investigate.md) with a properly framed window and service. Half-doing an RCA inline produces a shallow root cause and an incomplete sweep.
5. **An all-clear must state its own limits.** "Nothing wrong" is a much stronger claim than it looks. Say what you checked, over what window, in which environment, and what you could *not* see — an unmonitored datastore or an uninstrumented service means the correct answer is "nothing wrong in what's observable," and the difference matters.
6. **Don't let a clean sweep overwrite a known-issue.** Long-standing problems in `known-issues.md` that are still present should be restated as still-present, not silently dropped because they aren't new.

## 6. Output: the health report

A sweep produces a short report, not an incident folder. Keep it to one screen unless findings demand more.

```markdown
# Health sweep — <environment> — <YYYY-MM-DD HH:MM UTC>

**Scope:** <breadth> · **Window:** <window> · **Baseline:** <what it was compared against>
**Verdict:** healthy | degraded | incident

## Findings
| # | Signal | Observed | Baseline | New? | Trend | Blast radius |
|---|--------|----------|----------|------|-------|--------------|
| 1 | ...    | ...      | ...      | ...  | ...   | ...          |

## Not observable
<services/datastores with no usable signal, and what would fix that>

## Recommended next step
<escalate finding #N to an investigation | nothing to do | re-check after <window>>
```

Health reports are ephemeral by default — don't create an incident folder for a clean sweep. If a finding escalates into an investigation, the RCA folder becomes the durable record and the sweep is quoted in it as the originating signal.

If a sweep surfaces something the knowledge base should have told you (an unknown service, a new consumer group, an undocumented quirk), route it into `infra-knowledge/` per the self-review step in [`investigate.md`](investigate.md) §9 — that is how sweeps compound instead of repeating.
