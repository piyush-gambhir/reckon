# CLI issues — open items

Running log of bugs, gaps, and papercuts found in the `grafana`, `jenkins`, and `cubeapm` CLIs while using this workspace for real RCA work. Resolved items get removed as they land — the list is a to-do, not a history. See git log of each CLI repo for the fix context.

Legend: 🚧 in progress · ❌ open

_No open items as of 2026-05-26._

---

## How new items should be filed

Add a new section under the relevant CLI and use this shape:

```
### <legend> <n>. <short headline>
**Symptom:** exact command and the error / unexpected behaviour.
**Cause:** what's actually wrong (server quirk, CLI bug, doc drift).
**Impact:** who/what it blocks.
**Fix direction:** the likely path — flag, fallback, new endpoint.
```

Keep entries short. If the fix turns out to be long, link to a design doc rather than inline it.

Remove the entry when the fix lands, rather than flipping it to ✅ — the git log keeps the history.

---

## Recently closed (for context, not action)

**2026-06-12 fix pass** (uncommitted in each CLI repo as of writing — commit hashes to follow; binaries rebuilt + reinstalled to `~/.local/bin` same day):

- **grafana `datasource query` scalar/string results** — `resultType: "scalar"`/`"string"` (e.g. `--expr '1+1' --query-type instant`) failed the whole decode; now dispatched via custom UnmarshalJSON. Also: empty `resultType` accepted on the Loki streams path, and two `--from/--to` parsing bugs (ns-epoch read as seconds; float-form epoch-ms multiplied into year-52970).
- **jenkins `pipeline input-list` unmarshal crash** — wfapi returns `defaultParameterValue` as object/null/absent; was typed `string`. Same class as the old slaveAgentPort crash. Plus: `plugin check-updates` read `shortName` where update-center sends `name` (blank names, false negatives), and pipeline `changeSets` (WorkflowRun) were dropped from `build get`.
- **es `-o yaml` broken for ~25 commands** — `yaml.Marshal` of `json.RawMessage` emitted raw byte integers; now normalized through JSON first. Plus: `index alias list --index` filtered by alias name instead of index; `index list --status close` sent invalid `expand_wildcards=close` (→ `closed`); pending-tasks "Time In Queue" always empty (missing `human=true`).
- **nginxpm bool-decode crash against NPM ≤ 2.11.x** — NPM returns DB booleans as `0`/`1` until v2.12.0; every `list`/`get` failed to unmarshal. Now tolerant `Bool` type across 24 fields, with the repo's first regression tests.
- **cubeapm naive timestamps parsed as UTC** — `--from 2026-06-12T14:30:00` (no zone) shifted the window by the local UTC offset (+5:30 here); **this was the likely cause of the long-standing "narrow 5-min `logs query` returns 0 while `logs hits` shows thousands" quirk**. Now parsed as local time. Plus: `--service X` + `error OR timeout` built `service:X AND error OR timeout` (precedence bug — user query now parenthesized), and `decodeCubeID` tried base64 before hex so valid hex trace/span IDs were systematically corrupted. All with regression tests.

- **jenkins `status` slaveAgentPort unmarshal crash** — fixed in jenkins-cli `bdb2f63` (2026-03-23); binary rebuilt + reinstalled 2026-05-26.
- **jenkins `build info` alias** — jenkins-cli `1e76004` (2026-05-26). `jenkins build info <job> <num>` works as alias of `build get`.
- **grafana `datasource query` Loki vector/matrix unmarshal crash** — grafana `1999f52` (2026-05-26). Decodes `Data.Result` as `json.RawMessage` and dispatches on `resultType` (streams / vector / matrix).
- **grafana `annotation list --from/--to` silently treating RFC3339 as 0** — already fixed in source via `parseAnnotationTime` helper.
- **cubeapm `logs query -o json` NDJSON output** — already documented in `--help`. Callers: `jq -s`.
- **cubeapm retention not discoverable** — cubeapm-cli `6da0485` (2026-05-26). New `cubeapm logs status` subcommand probes 24-h buckets and reports `earliestNonZeroBucket`, `latestNonZeroBucket`, `retentionHours`, with a note when the earliest bucket suggests the retention horizon. Optional `--query` for service-scoped probes. Smoke-tested against the production CubeAPM deployment: cluster-wide retention ~32 h.
