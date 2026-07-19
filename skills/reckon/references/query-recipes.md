# Query recipes

Indexed by *what you're trying to find out*. Copy, substitute placeholders, run. All PromQL assumes CubeAPM's `cube_apm_*` metric family; LogsQL assumes VictoriaLogs-compatible syntax.

Placeholders: `<SVC>` = service label value, `<ROOT>` = exact `root_name`, `<ROOT_REGEX>` = regex, `<FROM>`/`<TO>` = RFC3339 UTC timestamps.

## Apdex / SLO

**Apdex score over time:**
```
(sum(rate(cube_apm_apdex_calls_total{service="<SVC>"}[5m]))
 - sum(rate(cube_apm_apdex_penalty_total{service="<SVC>"}[5m])))
 / sum(rate(cube_apm_apdex_calls_total{service="<SVC>"}[5m]))
```

**Apdex penalty attributed to endpoint:**
```
topk(15, sum by (root_name) (
  rate(cube_apm_apdex_penalty_total{service="<SVC>"}[10m])
))
```

**Calls and penalties (sanity check that penalty ≤ 2×calls):**
```
sum(increase(cube_apm_apdex_calls_total{service="<SVC>",root_name=~"<ROOT_REGEX>"}[<WINDOW>]))
sum(increase(cube_apm_apdex_penalty_total{service="<SVC>",root_name=~"<ROOT_REGEX>"}[<WINDOW>]))
```

## Endpoint latency

**Per-call server-span average latency over time (2-min buckets):**
```
sum(rate(cube_apm_latency_sum{service="<SVC>",root_name="<ROOT>",span_kind="server"}[2m]))
/ sum(rate(cube_apm_latency_count{service="<SVC>",root_name="<ROOT>",span_kind="server"}[2m]))
```

**Endpoint request rate over time:**
```
sum(rate(cube_apm_latency_count{service="<SVC>",root_name="<ROOT>",span_kind="server"}[2m]))
```

**P99 via histogram_quantile (when buckets are properly populated):**
```
histogram_quantile(0.99,
  sum by (le) (rate(cube_apm_latency_bucket{service="<SVC>",root_name="<ROOT>",span_kind="server"}[5m])))
```

## Sub-span breakdown

**Top sub-spans by total time inside an endpoint:**
```
topk(15, sum by (span_name, group_name) (
  rate(cube_apm_latency_sum{service="<SVC>",root_name=~"<ROOT_REGEX>",span_kind!="server"}[10m])
))
```

**Sub-span call rate (detect "N queries per request" patterns):**
```
sum(rate(cube_apm_latency_count{service="<SVC>",span_name="<SPAN_NAME>"}[2m]))
```

**Single sub-span latency over 24 h (baseline check):**
```
sum(rate(cube_apm_latency_sum{service="<SVC>",span_name="<SPAN_NAME>"}[5m]))
/ sum(rate(cube_apm_latency_count{service="<SVC>",span_name="<SPAN_NAME>"}[5m]))
```

## Caller discovery

**Which services are calling `<HOST>` (outbound HTTP spike):**
```
topk(10, sum by (service) (
  rate(cube_apm_latency_count{group_name="HTTP <HOST>",span_kind="client"}[2m])
))
```

**Find services with a call-rate jump vs baseline** (post-process the range query; there isn't a clean PromQL expression for peak/baseline ratio — pull the range and compute client-side).

## Traces

**Slow traces for a service:**
```
cubeapm traces search --service <SVC> --env PROD --span-kind server \
  --min-duration 1s --from <FROM> --to <TO> --limit 50 -o json
```

**Error traces for a service:**
```
cubeapm traces search --service <SVC> --env PROD --span-kind server \
  --status error --from <FROM> --to <TO> -o json
```

**Note:** some CubeAPM deployments require `--index cube:latency` or `cube:error`. If the CLI fails to unmarshal the response, see `server-quirks.md` in `infra-knowledge/`; fall back to curl with the saved session cookie.

## Logs

**Recent errors for a service:**
```
cubeapm logs query 'level:error' --service <SVC> --last 1h -o json
```

**Error distribution by service (stats pipe):**
```
cubeapm logs stats 'level:error | stats by (service.name) count() as c' --last 1h -o json
```
(The form `stats count() by (service.name)` fails on some CubeAPM servers — prefer the `by (..) count() as name` shape.)

**Log volume time-series:**
```
cubeapm logs hits --query 'level:error AND service.name:<SVC>' --last 6h --step 15m -o json
```

## Grafana

**Annotations in a window (epoch-ms!):**
```
FROM_MS=$(date -j -u -f "%Y-%m-%dT%H:%M:%SZ" "2026-04-19T13:00:00Z" +%s)000
TO_MS=$(date -j -u -f "%Y-%m-%dT%H:%M:%SZ" "2026-04-19T14:30:00Z" +%s)000
grafana annotation list --from $FROM_MS --to $TO_MS --type alert -o json
```

**All alert rules, filter by keyword:**
```
grafana alert rule list -o json | jq '.[] | select(.title | test("<PATTERN>"; "i"))'
```

## Jenkins (deploy check)

**Recent builds for a service's deploy job:**
```
jenkins build list <DEPLOY_JOB> -o json | jq '.[:10]'
```

**Build log for a specific build:**
```
jenkins build log <DEPLOY_JOB> <BUILD_NUMBER>
```
