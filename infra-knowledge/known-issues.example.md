# Known latent issues

Pre-existing slow queries, fragile endpoints, architectural patterns that bite under load. **These are not new bugs** — they're long-standing and the team is aware. An incident often exposes one of these; the right response is usually "incident triaged, latent issue tracked separately" rather than "new fire". Copy this file to `known-issues.md` and fill in real issues.

When you add an entry: include a **date**, a **pointer to the RCA or ticket** where it surfaced, and **a number**.

## Slow queries

### Example entry
**Observed:** `<query span_name>` on `<service>` — avg 200–900 ms per call, **all day long**. Range does not correlate with load or time of day.
**First surfaced:** [RCA YYYY-MM-DD](../RCA-YYYY-MM-DD-<slug>.md) — amplified under bulk load.
**Why it matters:** at ~600 ms per call, each MySQL connection can sustain ~2 q/s of this query before queueing begins. Any fan-out pattern above ~4 q/s saturates the pool.
**Suspected cause:** missing index on the `WHERE` predicate. Needs DBA `EXPLAIN`.
**Mitigation status:** not fixed as of YYYY-MM-DD.

## Fan-out endpoints (cascade risk)

### Example entry
**Endpoint:** `POST /api/do-thing` on `<service>`.
**Shape:** Synchronous orchestrator. Each call fans out N+ blocking HTTP hops to downstream services.
**Cascade risk:** ⭐⭐⭐⭐. Any slowdown in a single downstream multiplies into this endpoint's user-visible latency.
**Typical baseline:** `<latency>`.
**Under fan-in from `<bulk endpoint>`:** `<much worse>`.
**Mitigation path:** parallelize independent downstream calls, or move the fan-out to a queue.

## Services with known data-quality issues

List any systemic inconsistencies — duplicated/cased label values, missing `env`, stale metric series, etc.

## Logs not shipped to CubeAPM

If `cubeapm logs query --service <X>` returns nothing, verify the service actually ships logs:

```
cubeapm logs field-values service.name --last 2h | grep -i <X>
```

Services that don't appear in that list log somewhere else (CloudWatch, Loki, ELK, stdout). Don't conclude "no errors" from an empty CubeAPM log query — check the service's actual log destination.
