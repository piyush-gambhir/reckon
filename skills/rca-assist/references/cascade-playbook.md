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
- [ ] Deploys? (`grafana annotation list` for `deploy` tag + `jenkins build list` for the service's deploy job. Skip if the alert isn't a Grafana rule — see SKILL.md §6 #8 and `infra-knowledge/server-quirks.md` for workspace-specific notification-template patterns.)
- [ ] Upstream-outage-induced retry storm? (Check upstream service's Apdex; if it dropped just before yours, retries are the story.)

### Caller discovery for `/internal/` / service-to-service endpoints

If the failing root_name is `/internal/`, `/private/`, `/api/internal/...`, or otherwise namespaced as service-to-service, identify the caller before writing the RCA. Try in order; each takes ~30s:

1. **HTTP client spans pointing at the endpoint.** The most reliable when callers are OTel-instrumented:
   ```
   sum by (service) (rate(cube_apm_latency_count{env="PROD",span_kind="client",
     span_name=~".*<svc>:.*<endpoint-path>.*"}[10m]))
   ```
   `span_name` for client spans on this stack uses `<METHOD> <SERVICE>:<root_name>` — search for the destination service substring.
2. **Inbound `context.ip` / `context.userAgent` from a sample error log.** Multiple distinct IPs with `userAgent=node` means a Node service in the same VPC.
3. **Resolve IP → service** via:
   - `count by (service) (cube_apm_latency_count{env="PROD",hostname=~"ip-<dashed-ip>.*"})` (uses `hostname` label; tries `host.name` too with `"host.name"=~...`)
   - Fall back to ops/infra: `kubectl get pods -A -o wide | grep <ip>` or `aws ecs describe-tasks`
4. **Trace by `trace.id` from a fresh error log.** `cubeapm traces get <id>`. **Do this fast** — tail sampling on internal/low-volume routes drops traces within a few minutes.
5. **If all four fail, that's still a finding.** Document it as: "Caller service is not currently observable in CubeAPM under any of `span_kind=client`, `hostname=`, `host.name=`, or retained traces." That tells the team a real instrumentation gap, not "we couldn't find it."

This step is missed often because the obvious cause (e.g., a Twilio/upstream rejection) explains the *failure* completely, but you still want the caller identified for blast-radius assessment and for the post-fix verification ("did the caller's success rate recover too?").

## Level 7: close

- [ ] Statement of cause: one sentence connecting trigger → propagation → root.
- [ ] List of latent issues surfaced (things that were broken before but quiet).
- [ ] What would prevent recurrence, ranked by leverage.
- [ ] Data gaps explicit.
