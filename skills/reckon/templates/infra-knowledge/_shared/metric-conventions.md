# Metric and logging conventions

Label names, metric names, and value conventions observed on **your** CubeAPM instance. Copy this file to `metric-conventions.md` and fill in real values.

## CubeAPM metric families (typically stable)

These metric families ship with CubeAPM out of the box. Confirm they exist in your deployment before assuming anything here applies:

### `cube_apm_apdex_*`
- `cube_apm_apdex_calls_total` — counter, one increment per call scored by Apdex
- `cube_apm_apdex_penalty_total` — counter, penalty weighted by tolerance

**Apdex formula:**
```
(calls_rate - penalty_rate) / calls_rate
```

### `cube_apm_latency_*`
Histogram: `cube_apm_latency_bucket`, `cube_apm_latency_count`, `cube_apm_latency_sum`.

**Common labels:** `category`, `env`, `group_name`, `host.name`, `http_code`, `root_name`, `service`, `span_kind`, `span_name`.

**Span-kind semantics (important distinction):**
- `span_kind="server"` — server-side view of a root transaction. Use this for **per-endpoint latency**.
- `span_kind="client"` — outbound HTTP to another service. Use this for **caller discovery**.
- `span_kind="internal"` or empty — inside-process spans (DB calls, etc.).

## Label conventions in **this** deployment

| Label          | Values seen                                  | Notes |
|----------------|----------------------------------------------|-------|
| `env`          | e.g. `prod`, `stage`, `dev`                  | Document the exact spellings used here |
| `service`      | see [../env/services.md](../env/services.md)               | Case-sensitive; note any inconsistencies |
| `service.version` | typically version tags or `unset`          | |
| `root_name`    | e.g. `WebTransaction/...`                    | Framework-dependent format — document the shape |

## Log conventions (VictoriaLogs / LogsQL)

Document:
- Which field holds the service name in logs (usually `service.name`, distinct from the metric label `service`).
- Whether log service values match metric service values (case, separator).
- Which services are **not** shipping logs to CubeAPM (check with `cubeapm logs field-values service.name`).
- Standard log level field (`level`, `severity`, etc.).

### LogsQL gotchas

Record any parser quirks encountered. Example:

- `count() by (field)` may fail parsing — prefer `by (field) count() as c`.
- Queries need a non-empty filter; use `*` for wildcard.

## Apdex tolerance thresholds

Document per-service Apdex tolerance if known. When `penalty_rate ≈ calls_rate`, every call exceeded tolerance — that tells you the order of magnitude of the latency number the alert was tuned for.

## Time

Which time zone(s) the team works in, and how often clocks are confused:

- Storage: CubeAPM / Grafana store UTC.
- Display: your alert channels typically show _____ (document here).
- Include both local and UTC in RCAs.
