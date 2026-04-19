# Cascade playbook

Per-level checklist. At each level, answer two questions: *where did the time/errors go* and *what fed this level*. Skip to the next level only when you can state an attribution quantitatively.

## Level 0: frame

- [ ] Exact service label value (match the metric, not the user's prose)
- [ ] UTC start / UTC end of the window
- [ ] Symptom expressed as a number (Apdex 0.21, p99 2.5s, 5xx rate 8%, queue lag 80k)
- [ ] Check `infra-knowledge/services.md`, `metric-conventions.md`, `server-quirks.md`, `known-issues.md`

## Level 1: service-wide signal

- [ ] Apdex or equivalent over the window + 30 min buffer each side, 2-min buckets
- [ ] Did the baseline recover? If yes, note the recovery timestamp.
- [ ] Are other services affected at the same time? (Query the same metric with `topk` by service for the window.) If yes, consider a shared dependency.

## Level 2: endpoint attribution

- [ ] Topk endpoints by Apdex penalty / error count / slow-call count
- [ ] Single endpoint >80% of the damage → focus there
- [ ] Otherwise: are the top endpoints similar (share a downstream, similar DB access)? If yes, aggregate them.

## Level 3: per-call latency vs call volume

- [ ] Per-call latency over time for the endpoint (server-span avg)
- [ ] Call rate over time for the endpoint
- [ ] Compute ratios: peak/baseline for each
  - Per-call latency ratio >>1 → downstream-induced; recurse to the next service
  - Call rate ratio >>1 → find the caller (Level 5)
  - Both changed → expect a positive-feedback cascade

## Level 4: sub-span attribution

- [ ] Topk sub-spans by total ms/s during the window
- [ ] Group by `group_name` (HTTP downstream / DB / queue) to see the pattern
- [ ] For each top sub-span, note: is this one call per request, or N calls? (A DB query with rate 5× the endpoint rate means each request makes 5 queries — often the real problem.)

## Level 5: recurse into downstream

**If downstream is an HTTP service:**
- [ ] Apply Level 1–4 to that service, same window
- [ ] Compare its *caller* rate (§3.6 in SKILL.md) during the window vs its baseline
- [ ] Check `infra-knowledge/known-issues.md` for known behaviors of that service

**If downstream is a DB query:**
- [ ] Plot `rate(sum)/rate(count)` for the query over 24 h to see the baseline
- [ ] Plot `rate(count)` for the query over 24 h to see call-rate baseline
- [ ] If per-query latency is stable-but-slow → latent bug; the incident just amplified it
- [ ] If per-query latency jumped at incident start → DB-side event (index rebuild, replication lag, lock contention)
- [ ] Cross-check: other services querying the same table — does any of them see the same effect?

**If downstream is a queue:**
- [ ] Consumer lag metric over the window
- [ ] Producer rate spike before the lag rose

## Level 6: identify the trigger

- [ ] Scheduled job rate changes? (Compare CADENCE-* service rates across the window.)
- [ ] Admin console activity? (Look for reprocess / bulk / admin GET/POST on the relevant screens in the minute preceding the spike.)
- [ ] One-off bulk calls? (Search `root_name=~".*bulk.*"` or `.*reprocess.*` for a single call at incident start.)
- [ ] Deploys? (`grafana annotation list` for `deploy` tag + `jenkins build list` for the service's deploy job.)
- [ ] Upstream-outage-induced retry storm? (Check upstream service's Apdex; if it dropped just before yours, retries are the story.)

## Level 7: close

- [ ] Statement of cause: one sentence connecting trigger → propagation → root.
- [ ] List of latent issues surfaced (things that were broken before but quiet).
- [ ] What would prevent recurrence, ranked by leverage.
- [ ] Data gaps explicit.
