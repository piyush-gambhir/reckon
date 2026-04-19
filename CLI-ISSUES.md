# CLI issues surfaced during RCA investigations

Running log of bugs, gaps, and papercuts found in the `grafana`, `jenkins`, and `cubeapm` CLIs while using this workspace for real RCA work. Fix one at a time.

Legend: ✅ fixed · 🚧 in progress · ❌ open

---

## cubeapm-cli (`piyush-gambhir/cubeapm-cli`)

### ✅ 1. Login connection test depended on an optional Jaeger endpoint
**Symptom:** `cubeapm login` succeeded auth, then failed the connection test with
`API error (HTTP 400): unsupported path requested: "/api/v1/services"`, refusing to save the profile.
**Cause:** `runLogin` called `GetServices()` (`/api/traces/api/v1/services`). CubeAPM's Jaeger-compatible layer does not guarantee `/services`; the UI at `cube.spyne.ai` doesn't use it either. Reverse proxy stripped `/api/traces` and backend rejected `/api/v1/services`.
**Fix:** switched the test to `GetLabels()` (`/api/metrics/api/v1/labels`) — Prometheus API is a stable contract every CubeAPM server serves.
**Files:** `cmd/login.go:205`, `docs/CREDENTIALS.md`.

### ✅ 2. `traces search` missing required `index` query param
**Symptom:** `cubeapm traces search --service X --last 1h` → `HTTP 400 index is required`.
**Cause:** CubeAPM's search endpoint multiplexes storage backends (`cube:latency`, `cube:error`). The CLI never sent `index`.
**Fix:** added `--index` flag (default `cube:latency`). `cmd/traces/search.go`, `internal/client/traces.go`, `internal/client/traces_test.go`.

### ✅ 3. `traces search` did not pass `env` as a top-level query param
**Symptom:** even with `--env PROD`, request failed with `HTTP 400 env is required`.
**Cause:** CLI put `env` into the `tags` JSON blob only. Server needs it at the top level (`&env=PROD`).
**Fix:** in `SearchTraces`, promote `tags["environment"]`/`tags["env"]` to a standalone `env` query param.
**Files:** `internal/client/traces.go`.

### ✅ 4. `traces search` response unmarshals with wrong type
**Symptom:** After fixes 2 and 3, request returns HTTP 200, but the CLI fails with
`json: cannot unmarshal array into Go value of type types.JaegerSearchResponse`.
**Cause:** Server returns a raw JSON array: `[{ "keySpanId": "...", "trace": { "spans": [...], "processes": {...} } }, ...]`. CLI expects the Jaeger wrapper `{ "data": [...], "errors": [...] }`.
**Impact:** `cubeapm traces search` is unusable against cube.spyne.ai. Workaround is raw `curl` with the saved session cookie.
**Fix:** `SearchTraces` now branches on the top-level JSON shape (object → Jaeger; array → CubeAPM native) and converts the native format to the existing `TraceSearchResult` in the client. Snake_case field names, base64 trace/span IDs, RFC3339 `start_time`, and typed `v_*` tags are all normalised. Works against both server types. `internal/types/trace.go`, `internal/client/traces.go`.

### ✅ 5. `traces search` requires `spanKind` but CLI treats it as optional
**Symptom:** `HTTP 400 spanKind is required` unless `--span-kind server` is passed.
**Cause:** server requires `spanKind`; CLI sends empty string when flag is unset.
**Fix:** `--span-kind` now defaults to `server` (the 99% case). Users investigating client-side or internal spans pass the explicit value. `cmd/traces/search.go`.

### ✅ 6. `traces services` command is broken against this server (same reason as fix 1)
**Symptom:** `cubeapm traces services -o json` → `HTTP 400 unsupported path requested: "/api/v1/services"`.
**Fix:** `GetServices()` now falls back to `metrics label-values service` when the Jaeger endpoint fails. Returns the same `[]string` shape transparently. Against cube.spyne.ai this yields the 84-service inventory. `internal/client/traces.go`.

### ✅ 7. `logs stats` skill docs show syntax the server rejects
**Symptom:** `cubeapm logs stats '... | stats count() by (service.name)'` → 422 `cannot parse "stats" pipe: unexpected token "(" after [count(*)]`.
**Cause:** LogsQL syntax shown in `CLAUDE.md` / `skills/cubeapm/SKILL.md` is `count() by (field)`, but this server's parser accepts `stats by (field) count() as c`.
**Fix:** updated `cmd/logs/stats.go`, `cmd/logs/logs.go`, and `CLAUDE.md` examples to the working form. Users copy-pasting from the help text now get queries that parse.

### ✅ 8. `logs query --service <X>` silently returns nothing for services that don't log
**Symptom:** `cubeapm logs query --service MEDIA-SERVICE --last 1h` → "No logs found." — but the service is emitting metrics fine; it just doesn't ship logs to CubeAPM.
**Fix:** when `--service` is set and results are empty, emit a diagnostic hint pointing users at `logs field-values service`/`service.name` to distinguish "wrong name" from "service doesn't log to CubeAPM". `cmd/logs/query.go`.

### ✅ 9. `logs streams` requires a query but error is confusing
**Symptom:** `cubeapm logs streams --last 1h` → `HTTP 400 cannot parse query []: missing query; context: []`.
**Fix:** `GetLogStreams`, `GetLogFieldNames`, and `GetLogFieldValues` now default an empty `--query` to `*`. Same fix applies to the three client functions. `internal/client/logs.go`.

### ❌ 10. Service label values come back case-mangled across the metric space
Observation, not a CLI bug per se: `MEDIA-SERVICE`, `Media-Service`, `MEDIA-CONFIG`, and `MEDIA-HANDLER` are all distinct label values on `service`. The apdex metric is only emitted for `MEDIA-SERVICE` (upper). Worth a CLI helper like `cubeapm metrics label-values service --like media` or fuzzy matching.

---

## grafana CLI (`piyush-gambhir/grafana-cli`)

### ✅ 11. `annotation list --from/--to` are epoch-ms, but examples/skill doc imply RFC3339
**Symptom:** `grafana annotation list --from 2026-04-19T13:00:00Z --to 2026-04-19T14:30:00Z -o json` silently returned empty JSON, then crashed downstream parsers.
**Cause:** the flag was typed as `int64` (epoch ms). RFC3339 strings parsed as 0 → "no annotations in the Unix epoch window" → empty output.
**Fix:** `--from`/`--to` are now string flags that accept RFC3339, Unix seconds, epoch milliseconds, and epoch nanoseconds. The command normalises to epoch-ms internally. Help text updated with both formats. `cmd/annotation/list.go`.

### ✅ 12. Skill doc suggests "check Grafana annotations around the incident window" but for CubeAPM-native alerts there are no Grafana annotations
**Fix:** `grafana annotation list` help text now explicitly notes that CubeAPM-native alerts don't appear there. The `rca-assist` skill and `infra-knowledge/server-quirks.md` also carry the warning.

---

## jenkins CLI (`piyush-gambhir/jenkins-cli`)

### ❌ 13. Not blocking on login-test equivalents yet — haven't exercised it in this workspace
No bugs to report; `jenkins login` wasn't run in this workspace, so we couldn't close the "was there a 360-BACKEND deploy at 18:56 IST?" question in the media-service Apdex RCA. Follow-up: once logged in, check whether `jenkins status` / `jenkins job list` behave the same way cubeapm's connection test did (i.e., whether it uses an endpoint that's actually guaranteed to exist).

---

## Cross-cutting

### ❌ 14. No consistent way to dump "caller of an HTTP request" in CubeAPM
For the RCA we needed to know who called `POST /inventory/v2/bulk/reprocess-vins`. The CLI's `traces search` couldn't surface it (see #4), and `cubeapm metrics` only exposes `service` labels, not `client.service`. We had to infer via adjacent console-backend activity. A `cubeapm traces caller <root_name>` convenience command (or a dedicated caller-discovery example in the skill) would save time on recurring RCAs.

### ✅ 15. RCA workflow assumes `cubeapm traces services` works
`CLAUDE.md` at the repo root tells the agent to start with `cubeapm traces services` in step 1. Was broken on servers that don't expose `/services`.
**Fix:** covered by #6 — the fallback to `metrics label-values service` makes `traces services` work transparently. No workflow-level change needed.
