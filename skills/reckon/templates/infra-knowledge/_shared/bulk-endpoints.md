# Bulk / admin endpoints

Endpoints that — when invoked — generate sustained downstream load. A single call here can be the trigger for an incident that looks like "Service X's Apdex dropped" several minutes later. Copy this file to `bulk-endpoints.md` and fill in real endpoints.

When investigating an unexplained traffic spike, check for a call to one of these in the minute preceding it.

## Format

For each bulk endpoint, record:

- **Service + path**
- **Amplification** (1 call → N downstream calls over how long)
- **First seen** (RCA or incident reference)
- **Common initiator** (cron, admin user, integration partner, etc.)
- **Rate limit / backpressure in place** (yes/no)

## Example entries

### `POST /api/v1/bulk/<thing>` on `<service>`
**Amplification:** One call → ~N rps of downstream work for M minutes per K items. **No rate limiting**.
**First seen in:** `incidents/<YYYY-MM-DD>-<slug>/RCA.md`.
**Common initiator:** admin console user, via the `<screen>` page. Preceded by GETs to `/api/<screen>/<helper>`.
**Mitigation status:** `<status>`.

### `POST /api/admin/<reprocess>` on `<service>`
**Shape:** Single-item reprocess.
**Amplification:** ⭐ (single-item). Safer than bulk.

## Scheduled jobs (usually not triggers)

These tend to run at flat rates and are **not** incident triggers unless their rate changes. Check for rate changes in the incident window before blaming them.

- `<scheduler-service>` `<job-name>` — steady ~X rps

## How to detect a bulk trigger

At the time just before the incident started, look for a single-digit number of calls on any `.*bulk.*` or similar root_name:

```
topk(10, sum by (service, root_name) (
  increase(cube_apm_latency_count{root_name=~".*(bulk|import|reprocess).*",span_kind="server"}[5m])
))
```

Run at the incident-start timestamp. A single entry with count 1–5 is very often the trigger.
