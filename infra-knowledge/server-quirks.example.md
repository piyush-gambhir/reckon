# Server and CLI quirks

Behaviors of your specific CubeAPM / Grafana / Jenkins deployments and their reverse proxies that force the CLIs into workarounds. Copy this file to `server-quirks.md` and record real issues you hit.

Every entry should have: **the symptom**, **the cause**, **the workaround**.

## CubeAPM

### Example: reverse proxy strips a path prefix
**Symptom:** Requests to `/api/traces/api/v1/<anything>` return `HTTP 400 unsupported path requested: "/api/v1/<anything>"`.
**Cause:** The load balancer fronting CubeAPM only forwards a subset of Jaeger endpoints and strips the prefix before forwarding.
**Consequence:** `cubeapm traces services` falls back to `metrics label-values service` automatically (as of the response-shape fix). Writing new tooling against `/api/traces/*` requires checking which paths the server actually routes.
**Workaround:** The CLI handles this transparently. For raw HTTP, use only paths the web UI uses — that's the guaranteed-available surface.

### Example: all CubeAPM APIs share one port
**Symptom:** `cubeapm login` connection test tries port 3140, times out.
**Cause:** Internal ports (3140/3130/3199) are not exposed publicly; traffic routes through 443.
**Workaround:** At login, set all three port prompts to 443.

### Example: trace search response is not Jaeger-standard
**Symptom:** CLI historically failed to unmarshal the response.
**Cause:** Server returns a native array shape instead of the Jaeger `{data, errors}` wrapper.
**Workaround:** Fixed in the CLI — `SearchTraces` now branches on the top-level JSON shape and converts native responses to Jaeger-style results transparently.

### Example: sampling retention for low-volume endpoints
**Symptom:** `traces search` returns no results for a root_name you know had a call.
**Cause:** Tail sampler configuration — admin / bulk endpoints often aren't retained.
**Workaround:** Accept the data gap in the RCA. Fall back to application access logs for request-level detail.

## Grafana

### Example: CubeAPM-native alerts don't show in Grafana annotations
**Symptom:** `grafana annotation list --type alert` returns empty for an incident the user clearly saw.
**Cause:** CubeAPM's own alerting doesn't write annotations back to Grafana.
**Workaround:** Check the originating system's alert surface, not Grafana annotations.

## Jenkins

*(add as you find them)*

## Folder-scoped credentials (this workspace)

`rca-assist` is configured to use this directory's `.config/` via direnv (`export XDG_CONFIG_HOME="$(pwd)/.config"`). Every CLI reads/writes its config there, keeping credentials isolated from your global setup. If a CLI isn't seeing a session, confirm `direnv allow` ran and that `echo $XDG_CONFIG_HOME` points inside this workspace.
