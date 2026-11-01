# Reckon + CLI dependency and compatibility audit

Checked 2026-09-06 against public Go/npm registries, upstream release tags, and official API documentation.

## Scope

Six custom Go CLIs (CubeAPM, Elasticsearch, Grafana, Jenkins, Jira, Nginx Proxy Manager), their six documentation sites, and Reckon's setup scripts and documentation site. OpenSRE and globally installed third-party tools are outside this pass. Existing local Reckon edits were preserved. Task changes are committed and pushed in all seven repositories. All six CLI releases and all seven documentation sites are published. Unrelated pre-existing local edits remain uncommitted.

## Dependency baseline

| Component | Updated baseline |
| --- | --- |
| Go source / selected toolchain | Go 1.26 minimum / Go 1.27.1 |
| Cobra / pflag | 1.10.2 / 1.0.10 |
| flock / renameio v2 | 0.13.1 / 2.0.2 |
| YAML | maintained `go.yaml.in/yaml/v3` 3.0.5 |
| x/sys / x/term | 0.47.0 / 0.45.0 |
| Jenkins and Jira tablewriter | 1.1.4, migrated from the 0.0.5 API |
| Next.js / React | 16.3.4 / 19.2.8 |
| Fumadocs core and Base UI / MDX | 16.15.7 / 15.4.0 |
| TypeScript / Tailwind / Zod | 7.0.2 / 4.3.3 / 4.5.4 |
| Wrangler / pnpm | 4.129.0 / 11.25.0 |
| govulncheck / Staticcheck | 1.7.0 / 0.8.1 |
| GoReleaser / Syft | 2.18.0 / 1.51.1 |

Other direct docs dependencies were refreshed to the registry's latest stable releases. The six sites have matching dependency specifications; Reckon retains its exact font pins at the same current versions. All manifests and lockfiles were updated together. GitHub Actions are pinned to verified release commit hashes; dependency update PRs are configured weekly.

## Fixes beyond version bumps

- **Correct executable installation:** all six `make install` targets now build the public binary name. Their main package directory is named `cli-go`, so the previous `go install .` targets could overwrite a shared `cli-go` executable. `INSTALL_DIR` and `GOBIN` are honored.
- **Working Reckon release pins:** Bash and PowerShell share [cli-releases.csv](../scripts/cli-releases.csv). It maps the latest public releases (Grafana/Jenkins 0.2.10, CubeAPM 0.2.11, Elasticsearch 0.1.11 as of 2026-10-05) to matching nested-module semantic version tags. Private staging directories prevent binary collisions. Setup remains idempotent and leaves already-installed commands in place.
- **CubeAPM request contract:** trace search sends `query`; trace search/fetch send Unix seconds as documented. Promoting the environment filter no longer mutates the caller's tag map. Jaeger-shaped output still uses its existing internal time representation. Contract tests cover the request units and repeated use of filters.
- **Table output:** Jenkins/Jira use the current tablewriter API, preserve borderless output, and propagate writer errors. Tests cover long values, Unicode content, empty slices, pointers to slices, JSON fallback, and broken output writers.
- **Maintained YAML:** all six CLIs use the maintained YAML organization v3 package, with existing config/output tests retained.
- **Fumadocs search migration:** replaced the obsolete Orama initializer with the current default static search client and removed the direct Orama dependency. A built-index test exercises the installed search client against each site's actual exported index.
- **Docs and CI:** corrected stale Go/source-install instructions and development-version examples; added compatibility pages, top-level command documentation checks, docs builds/audits, search-index tests, and named-executable installation checks to CI.

## Validation

| Check | Result |
| --- | --- |
| All six CLIs: `go mod verify`, `go mod tidy -diff` | Passed |
| All six CLIs: `go vet`, full `go test -race -count=1` | Passed |
| All six CLIs: govulncheck and Staticcheck | Passed; no reported reachable vulnerabilities |
| All six CLIs: existing top-level `check-llms.sh` | Passed |
| All six `make install` targets, temporary destinations, `version` smoke tests | Passed |
| Reckon: actual installation of all four pinned public releases into a temporary directory | Passed; expected version output |
| Reckon: 10 existing control-surface tests | Passed |
| Reckon: new mocked installer tests | Passed: naming, GOBIN preservation, spaces in paths, failure cleanup, idempotency |
| All seven docs sites: TypeScript checks and production static builds | Passed |
| All seven docs sites: exported static search-index checks | Passed |
| All seven docs sites: frozen-lockfile installs | Passed |
| All seven docs sites: production dependency audit | No known vulnerabilities reported |
| Workflow YAML parsing and `git diff --check` | Passed |

Go/application tests use local fixtures and mock HTTP servers. Production infrastructure was not queried. PowerShell passed the GitHub CI parser gate; native Windows execution was not available locally. All seven GitHub CI pipelines and all six release workflows passed. Release archives, checksums, SBOMs, and build provenance attestations were published. All four newly tagged Reckon installs were verified in an isolated temporary directory.

## API references and compatibility limits

| CLI | Reference and remaining boundary |
| --- | --- |
| CubeAPM | [Official trace API](https://docs.cubeapm.com/http-apis/traces-apis); rolling docs, not a versioned manual. Native request parameters are now the default; optional Jaeger-derived operations and extra filters remain deployment-dependent. |
| Elasticsearch | [REST API v9](https://www.elastic.co/docs/api/doc/elasticsearch/v9/), upstream 9.5.3; features and SQL still depend on server version/license. |
| Grafana | [Grafana 13.2 HTTP API](https://grafana.com/docs/grafana/v13.2/developer-resources/api-reference/http-api/), upstream 13.2.1. The CLI still uses deprecated `/api` routes. Full adaptation to versioned `/apis` resources remains separate work; it is not claimed complete. |
| Jenkins | [Remote Access API](https://www.jenkins.io/doc/book/using/remote-access-api/) and [CSRF rules](https://www.jenkins.io/doc/book/security/csrf-protection/); controller/plugin-specific contracts have no single REST API version. |
| Jira | [Cloud REST v3 enhanced search](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/); existing Cloud cursor pagination and explicit fields are current. Data Center retains its separate classic v2 search. |
| Nginx Proxy Manager | [2.15.1 release-tagged schemas](https://github.com/NginxProxyManager/nginx-proxy-manager/tree/v2.15.1/backend/schema); no independently versioned stable public API contract. |

Dependency freshness and mock contract checks are not an exhaustive certification against every deployed server. Grafana's full `/apis` migration and live-server acceptance checks are the principal remaining compatibility work. Go 1.27-built macOS binaries require macOS 13 or later ([release notes](https://go.dev/doc/go1.27)).

## Recheck

In each `cli-go/` directory, run `go list -m -u all` for new modules and the checks above before accepting upgrades. In each `web/`, run `pnpm outdated`, `pnpm audit --prod`, `pnpm types:check`, `pnpm build:cloudflare`, and `pnpm test:search`. When updating Reckon's release CSV, verify each nested-module version tag matches the corresponding public release tag and smoke-test the named executable in a temporary directory.

## Published releases

Updated 2026-10-05 to the latest release of each CLI.

| CLI | Release | Documentation |
| --- | --- | --- |
| cubeapm-cli | [0.2.11](https://github.com/piyush-gambhir/cubeapm-cli/releases/tag/v0.2.11) | [Live docs](https://projects.piyushgambhir.com/cubeapm-cli/docs/compatibility) |
| grafana-cli | [0.2.10](https://github.com/piyush-gambhir/grafana-cli/releases/tag/v0.2.10) | [Live docs](https://projects.piyushgambhir.com/grafana-cli/docs/compatibility) |
| jenkins-cli | [0.2.10](https://github.com/piyush-gambhir/jenkins-cli/releases/tag/v0.2.10) | [Live docs](https://projects.piyushgambhir.com/jenkins-cli/docs/compatibility) |
| es-cli | [0.1.11](https://github.com/piyush-gambhir/es-cli/releases/tag/v0.1.11) | [Live docs](https://projects.piyushgambhir.com/es-cli/docs/compatibility) |
| jira-cli | [0.1.12](https://github.com/piyush-gambhir/jira-cli/releases/tag/v0.1.12) | [Live docs](https://projects.piyushgambhir.com/jira-cli/docs/compatibility) |
| nginxpm-cli | [0.1.12](https://github.com/piyush-gambhir/nginxpm-cli/releases/tag/v0.1.12) | [Live docs](https://projects.piyushgambhir.com/nginxpm-cli/docs/compatibility) |

[Reckon installation documentation](https://projects.piyushgambhir.com/reckon/docs/installation) includes the shared release pins. Public documentation and search endpoints returned HTTP 200; all seven search exports matched the local builds.

## Native maintenance pass, 2026-10-22

This pass covers Reckon's current Rust implementation and documentation. These
changes are local and have not been published.

- Shared CubeAPM metrics queries now parse PromQL and check every selector,
  including aggregation parameters, range selectors and subqueries. A scoped
  expression cannot hide an unscoped read in arithmetic or a function argument.
  Fully scoped `or` and `unless` expressions remain usable.
- Shared metrics queries receive a linear preflight before parser construction:
  at most 64 KiB, a conservative 1,024-token count and 128 nested delimiters.
  Deep parentheses and aggregations that previously aborted with stack overflow
  now return an error before a provider read. Quoted content and comments do not
  consume nesting depth. Boundary regressions cover direct reads and typed
  collection, which retains its existing tighter parameter-size limit.
- Backtick literals are converted to equivalent quoted strings only for AST
  inspection, working around an upstream lexer issue with literal backslashes.
  Original query bytes reach the provider unchanged, and foreign or unscoped
  selectors remain refused. This follows the
  [PromQL raw-string contract](https://prometheus.io/docs/prometheus/latest/querying/basics/#string-literals).
- Reconciliation checks the original payload, target fingerprint and pinned
  account before accepting proof that a write landed. Credential rotation on
  the same target remains supported.
- Cancellation received at an approval prompt prevents subsequent subprocess
  startup.
- Session reads hold the session lock through the budget check, execution and
  evidence save. Evidence is saved before writing to caller pipes, so a closed
  pipe cannot discard a result or refund a read.
- Typed collection calculates its fresh batch before enforcing the session budget.
  Reused evidence adds no record and needs no additional budget; mixed retries
  reserve only their new records. The session lock also covers planning and
  credential preparation.
- Session reads validate saved budgets, lifecycle status, note identities and
  structure, workflow snapshots and copied previous-review actions. Malformed
  budgets cannot become unlimited reads, and damaged workflow or follow-up data
  cannot silently remove completion requirements. Invalid state is preserved.
- Closing as `done` or `diagnosed` requires evidence and note integrity checks to
  pass. A workflow with no remaining tasks still cannot close successfully when
  its findings cite missing or failed evidence.
- Plain tasks closed as `done` no longer fail `check` solely for having no finding,
  matching the existing close contract. Diagnosed sessions still need a finding,
  and workflow completion still enforces the recipe's requirements.
- Cache hits validate limits and respect the current output cap without splitting
  UTF-8. Invalid or oversized cache files trigger a fresh read. Results whose JSON
  encoding exceeds the state-file size limit are returned without being cached.
- Cache identity includes saved configuration, provider rules, executable content
  and exact stdin bytes. Typed collection reuse is also bound to the connection,
  resolved settings, configuration and executable, so a changed source cannot
  reuse another source's evidence.
- Prepared actions pin saved configuration and check it before the account probe
  and again after confirmation. Older actions without this fingerprint require
  operator review. A nonzero CLI exit after a write is `unknown` and can be
  reconciled; only a failure to start the CLI is a definite `failed` outcome.
- Applying an action reloads connection and production policy after confirmation,
  so changed account pins, narrowed limits or newly production environments
  cannot use stale approval. The CLI's canonical path and content are checked
  across the prompt and that executable is used for the write. Resolved
  credentials shown to the operator remain pinned for that attempt.
- Action expiry is checked before approval and again after confirmation at the
  exact 24-hour boundary. Future preparation timestamps are refused, and an action
  that expires at an open prompt becomes expired without starting the CLI.
- Action listing and loading validate identity, known status, preparation time,
  payload and receipt shape, preserving malformed records instead of presenting
  them as ordinary actions. Payload errors omit saved parameter values so a
  pinned listing cannot disclose another environment's fields. Listings and
  briefings validate record paths from the workspace root, refusing redirected
  action or session folders. Previous-review
  summaries skip redirected write records without reading their contents.
- Activity and inbox append paths reject hard links before modifying the file
  and restore private permissions on existing logs. Rejected inbox writes do not
  acknowledge unseen items.
- Log events are serialized before writing and appended under a file lock,
  preventing concurrent watches from interleaving JSON. A regression polls four
  watches concurrently and verifies all 400 inbox events remain intact.
- Malformed watch and routine registries produce errors instead of panicking or
  appearing empty. Malformed goal-file structures appear as invalid in briefs.
- Directory listings distinguish missing optional folders from invalid types and
  metadata I/O failures. History, briefs and preflight reject files where session,
  action or incident directories are expected. Preflight propagates knowledge
  directory errors and counts regular Markdown and JSON files, excluding symlinks
  and junctions.
  Its incident overview rejects redirected roots and excludes linked entries.
  Previous-review summaries warn when action receipts cannot be enumerated or
  validated, instead of silently presenting an empty write history. Copied
  follow-up actions remain intact, and reports include the same warning.
- Watch and routine registry mutations are locked, and active jobs cannot be
  removed mid-run. Watch locks outlive the state folder they protect, and a poll
  rechecks that its saved definition still exists before running.
- Folder-link and unlink mutations share a registry lock, preventing a successful
  update from overwriting another successful change. Contention leaves the
  registry unchanged and returns an explicit locked error. Link path keys must be
  absolute and free of parent traversal, with no duplicate normalized bindings.
  Invalid keys and non-file registry paths fail before choosing a context instead
  of falling back to the saved org. Malformed files remain unchanged.
  NUL bytes in stored keys and non-UTF-8 input or canonical paths are rejected,
  preventing lossy conversion from changing another folder's binding.
- JSON state writers check the final pretty-printed byte count, including escapes
  and the trailing newline, against the existing 8 MiB reader limit. An oversized
  write fails before creating a destination directory or replacing an existing
  file, preserving readable previous state. The exact size boundary remains valid.
  Writers also reject nesting beyond the reader's limit. Session creation validates
  its metadata before creating the session directory, and promotion validates the
  added incident link before publishing the incident snapshot. Rejected metadata
  leaves no incomplete session or incident and preserves the existing session.
- Migration refuses invalid UTF-8, NUL bytes and non-file environment inputs
  without printing their values. Config fingerprints hash each file separately,
  preventing file contents from mimicking filename boundaries and incorrectly
  sharing different configurations. Every candidate config is checked, including
  those omitted by automatic sharing; symlinks, redirected paths and traversal
  errors fail the import. Non-object session and evidence metadata return errors
  with destination cleanup instead of panicking or creating unusable state.
  Incident snapshots receive the same org/environment remapping as sessions,
  preserving evidence artifacts and making legacy narratives visible in history.
  Generated secrets are reopened by the runtime parser and must match the planned
  settings exactly before config is copied. Oversized secret files roll back the
  destination, and escaped source-path comments cannot inject extra settings.
  Directory-based config such as gog's is detected without environment tokens;
  AWS helper config alone no longer invents a Kubernetes connection.
  Unclaimed settings are tracked per environment, so importing a key elsewhere
  cannot hide its omission. Saved work must name an environment present in the
  legacy layout. Evidence must match its original session and environment before
  remapping, and copied artifacts pass the normal integrity checks. Mismatches
  and damaged artifacts fail the import with destination cleanup.
  Imported sessions and incident snapshots use the same metadata validator as
  normal session reads, covering schema, budgets, notes, workflow snapshots and
  previous-review actions. Session IDs must also match their directory names.
- Watches and routines persist explicit production confirmation and check it
  again before an unattended read or agent launch. Older production definitions
  without that field require removal and re-adding with `--production`. Watch
  polls also recheck that the provider does not require per-command approval.
- Watch baselines include the connection's source fingerprint, so changed accounts,
  saved profiles and executables do not reuse another source's seen-item history.
- Watch errors and failed scheduled agents return nonzero exit codes to cron or
  launchd. Lock I/O errors and unsafe lock files are errors; only real contention
  produces a normal overlap skip, following Rust's distinct lock outcomes.
  See the [standard library lock error contract](https://doc.rust-lang.org/std/fs/enum.TryLockError.html).
- Routine installation validates the saved schedule and rejects control characters
  in scheduler paths and environment values. macOS plist paths refuse symlinked
  directories and files; atomic replacement preserves hard-linked external files
  and existing LaunchAgents directory permissions. The printed bootstrap command
  quotes paths containing spaces and apostrophes.
- Daily quotas validate their saved request count and UTC date before reserving
  another call. Malformed and future-dated ledgers fail without changing state or
  contacting the API; only valid previous days reset the allowance.
- Routine launches validate the saved Claude runtime, whole-minute timeout
  (5-240), workflow and string parameters before starting an agent. Invalid saved
  settings remain unchanged and produce an error in the activity log.
- Agent usage counters require nonnegative whole numbers; Codex cached input
  cannot exceed total input. Totals use checked addition, and Claude records need
  an ID or UUID for deduplication. Malformed counters and overflow return errors
  without panicking or changing transcripts. Large unsigned budgets compare
  correctly. Price rates must be finite and nonnegative, and cost overflow is an
  explicit error.
- All-org briefings propagate usage-accounting errors, so an unreadable total
  cannot be reported as zero tokens.
- Usage scanning propagates directory traversal, file metadata, open and stream
  errors instead of silently omitting unreadable transcripts. Missing runtime
  directories remain optional. Log paths must resolve to regular files; Unix
  opens are nonblocking and recheck the open handle, preventing named pipes from
  hanging a report. Directories with log extensions and valid file aliases remain
  supported. Present non-object usage values fail even when empty or false;
  legacy null placeholders and empty objects remain supported. A malformed `prices.json`
  path is an error instead of an empty rate table. Budget warnings and all-org
  briefs preserve the same accounting errors without changing source data.
- Codex duplicate keys include the declared thread ID and parsed UTC event time,
  preventing simultaneous independent threads from merging and equivalent offset
  or fractional timestamps from counting twice. Counters are validated before
  deduplication, and conflicting duplicate observations produce an error. Copies
  of the same identified log still count once; older logs without IDs retain the
  anonymous fallback. Active and archived logs are both included, with the same
  org attribution and shared duplicate detection across directories. The ID and
  directory contracts were checked against upstream
  [SessionMeta](https://github.com/openai/codex/blob/ffda54f5b8d5595eb0963594de32e206f98cc77f/codex-rs/protocol/src/protocol.rs#L3276)
  and [rollout locations](https://github.com/openai/codex/blob/ffda54f5b8d5595eb0963594de32e206f98cc77f/codex-rs/rollout/src/lib.rs#L86).
- Codex `token_usage_record` receipts count each response once per owning thread,
  including multiple responses in one turn. Turn contexts supply the workspace
  and model. Copied fork receipts require an original and matching counters;
  same-thread replays use the earliest original timestamp across active and
  archived files, including archives older than the reporting window. Missing
  identifiers, consumed counters or timestamps and conflicting receipts fail
  accounting without changing transcripts. Unrelated workspace counters do not
  affect the selected org. Legacy counters before and after response receipts
  remain included. Pending response receipts reconcile against cumulative
  increments in file order, excluding only covered mirrors. Repeated snapshots
  preserve pending coverage; cumulative reconciliation also handles incomplete
  responses whose stored last-request counters remain stale. Ambiguous coverage
  across context boundaries returns an incomplete-usage error. This follows the upstream
  [response record schema](https://github.com/openai/codex/blob/ffda54f5b8d5595eb0963594de32e206f98cc77f/codex-rs/protocol/src/protocol.rs#L2413)
  and [record-before-counter ordering](https://github.com/openai/codex/blob/ffda54f5b8d5595eb0963594de32e206f98cc77f/codex-rs/core/src/session/mod.rs#L5134).
- Legacy copied histories follow embedded session metadata and owner-tagged
  settings, or the explicit ordinal boundary for copied subagent history.
  Metadata and settings supply workspace/model fallbacks when turn context is
  absent. Declared owner IDs are rechecked for every relevant history, including
  files whose initial workspace differs from their later contexts.
  Copied counters must match an available original observation and physical
  revision. Nested copies cannot move charges into another org or reporting
  window. Copied logs without a reliable ownership boundary return an incomplete
  usage error. Physical revisions have separate counter keys, so replacing a
  rollout does not merge new work with previously charged work. Historical
  charges remain included even when revert removes their turns from the retained
  history. Paginated references must resolve to an available physical rollout
  with the exact ordinal and decoded byte boundary; missing, truncated, malformed
  and cyclic references fail accounting. Unrelated workspace references do not
  break a selected org's report. The implementation follows the upstream
  [settings owner](https://github.com/openai/codex/blob/ffda54f5b8d5595eb0963594de32e206f98cc77f/codex-rs/protocol/src/protocol.rs#L2349),
  [ordinal contract](https://github.com/openai/codex/blob/ffda54f5b8d5595eb0963594de32e206f98cc77f/codex-rs/rollout/src/ordinal.rs#L23)
  and [immutable revert history](https://github.com/openai/codex/blob/ffda54f5b8d5595eb0963594de32e206f98cc77f/codex-rs/thread-store/src/local/revert_thread.rs#L15).
- Legacy cumulative snapshots count once per physical history. Rate-limit
  updates, context-size hints and zero-token estimates cannot repeat a charge or
  move it to another workspace or reporting window. The first nonzero
  observation supplies its age and model. Copied and referenced histories retain
  their inherited counter baseline, including nested reverts. Conflicting
  nonzero observations, malformed cumulative counters and unexplained decreases
  within one rollout fail accounting. Explicit context-window fills start a new
  accounting epoch with a zero billing baseline, while repeated fill snapshots
  preserve that epoch. Equal charges before and after a fill remain distinct,
  including copied and referenced histories. This follows the producer's
  [context-window fill](https://github.com/openai/codex/blob/ffda54f5b8d5595eb0963594de32e206f98cc77f/codex-rs/protocol/src/protocol.rs#L2465).
  Unidentified logs deduplicate within each file and
  preserve timestamp-based copy detection across files; observations without
  cumulative billing fields retain the legacy timestamp fallback. The producer
  explicitly repeats counters during
  [rate-limit updates](https://github.com/openai/codex/blob/ffda54f5b8d5595eb0963594de32e206f98cc77f/codex-rs/core/src/session/mod.rs#L5291).
- Codex cold `.jsonl.zst` rollouts are read directly with streaming Zstandard
  decompression. Decoded byte offsets preserve physical history references;
  simultaneous plain and compressed copies count once. The reader handles
  concatenated frames and skippable metadata, validates available checksums and
  rejects truncated or invalid frames and excessive decoder windows. Fixed
  synthetic fixtures include a compressed block from the reference Zstandard
  encoder, so tests need no compression executable. The decoder uses the Rust
  `ruzstd` 0.9.1 library and retains its window limit. This follows Codex's
  [plain/compressed reader contract](https://github.com/openai/codex/blob/ffda54f5b8d5595eb0963594de32e206f98cc77f/codex-rs/rollout/src/compression.rs#L63)
  and the [decoder's frame contract](https://docs.rs/ruzstd/0.9.1/ruzstd/decoding/struct.StreamingDecoder.html).
- Scanned transcript records must be valid UTF-8 JSON objects with a type.
  Claude assistant messages and consumed Codex payloads are validated before
  interpretation; Codex histories require session metadata with a workspace.
  Malformed headers, completed records and unfinished JSON tails return an
  accounting error instead of zero or partial totals. Scoped usage timestamps
  must parse, and error messages omit transcript contents and invalid values.
  JSON escapes and blank lines remain supported. Valid final records without a
  newline are accepted, and history positions use actual decoded byte lengths,
  including CRLF and multibyte UTF-8. Spend and all-org briefings propagate these
  errors; a single-org budget check warns that usage could not be checked.
  Each org's report uses one lookback interval ending at the start of the report
  for both runtimes. Future observations cannot inflate totals or streamed
  Claude message maxima.
- Usage attribution resolves existing workspace paths and normalizes historical
  paths, preventing `..` or symlinks from assigning another org's counters to the
  current one. Deleted workspace subdirectories still count after resolving
  their existing ancestors. Missing children beneath foreign symlinks and
  dangling symlinks cannot acquire the containing org's attribution.
- State directories are created with private Unix modes from the first mkdir.
  Org creation reserves its directory atomically, refuses existing directories,
  and migration cleanup only removes its own destination. Tree copies refuse a
  destination inside their source, preventing recursive copying.
- Goal briefings validate session identity and evidence hashes before reporting
  a numeric result. Damaged evidence produces an invalid status.
- Goal readings retain completeness, source-quality metadata and coverage
  warnings in JSON and text briefs. Their newest-read choice compares parsed
  instants, including timezone offsets; invalid observation times and foreign-org
  session metadata produce invalid readings without rewriting evidence.
  Comparisons preserve exact signed and unsigned 64-bit integer values, including
  comparisons with floats. Invalid direction types no longer default to `up`.
  Scientific notation remains intact when formatting numbers for display.
  Goal argument matches also require whole arguments or exact flag names and
  values. A site or period embedded in another assignment cannot satisfy a match.
- Evidence files remain within their readable size limit after redaction and
  UTF-8 replacement. Expanded output is saved as bounded UTF-8 prefixes with
  matching hashes and marked partial, so it cannot break later session reads or
  be cited as complete evidence.
- Displayed output and saved evidence share the requested combined stdout and
  stderr limit after redaction and UTF-8 replacement, including cache hits.
  Truncation returns exit status 3. Redaction preserves existing markers across
  secret replacements and repeated passes, and cached markers remain intact
  until the final output boundary.
- Output caps, timeouts and cancellation no longer expose trailing prefixes of
  known secrets in terminal output, saved evidence, action receipts or
  verification error details. Matching occurs before UTF-8 decoding and covers
  raw and JSON-escaped values, including partial Unicode bytes. Prefix fallback
  keeps repeated-byte secret matching linear. Complete cache entries are bounded
  at presentation, without being treated as interrupted source streams.
- `run --session` returns exit status 2 when an otherwise successful subprocess
  produces invalid declared JSON or a provider error payload saved as failed
  evidence. Subprocess failures retain their own exit codes.
- Search Console evidence preserves bounded source-quality headers for direct
  queries, comparisons and insights. Missing pages mark the evidence partial;
  preliminary data and pagination warnings appear in session checks and reports.
  Headers retain reporting periods, Pacific timezone, row counts and fetch counts
  when reported. Oversized source lists are explicitly partial, and quality
  handling cannot hide a provider error. The schema follows the pinned
  [GSC query envelope](https://github.com/piyush-gambhir/gsc-cli/blob/1da4dc3454261f5310a6fca0ab4b761bffef4798/cli-go/internal/analytics/query.go).
- Rust 1.89.0 successfully compiled all targets with the locked dependency graph,
  verifying the manifest's minimum version. CI now includes the same check.
- Review quality handling now covers Bing's current and previous periods,
  delayed crawl issues, Clarity recording samples and export caps, Jenkins
  recent-build lists and changing build states, GitHub list caps and projections,
  and metric-provider warnings. Missing days, sampled data and reached caps are
  partial; source warnings reach checks and reports. Metadata is bounded and
  cannot hide provider errors. The fixed Clarity cap and UTC reporting follow
  Microsoft's [Data Export API contract](https://learn.microsoft.com/en-us/clarity/setup-and-installation/clarity-data-export-api).
- GitHub attached `--json=fields` output is recognized, and projected `--jq` or
  `--template` output is saved as text rather than rejected as malformed JSON,
  following the [GitHub formatting contract](https://cli.github.com/manual/gh_help_formatting).
  Formatting and limit detection respects flag values and attached shorthands.
- Metric matrices validate sample pairs, finite timestamps, numeric values,
  label types and native histogram structure across every series, including
  those outside the 20-series summary. Malformed data is failed evidence.
  Non-finite floats and histogram samples are counted explicitly instead of
  becoming empty data, following the
  [Prometheus result formats](https://prometheus.io/docs/prometheus/latest/querying/api/#expression-query-result-formats).
  Finite means scale before summation to avoid overflow near `f64::MAX`.
  Label previews are bounded and explicitly shortened, preventing large labels
  from creating oversized evidence metadata while retaining the output artifact.
  Series without sample fields remain partial with unknown coverage, including
  fields omitted during adapter normalization.
- Typed structured operations now validate their provider envelopes, list
  containers and required identifying fields. Wrong structures can no longer count
  as successful typed collection; malformed output is failed evidence and
  collection returns 2. Synthetic provider-format fixtures cover all 18
  registered operation formats, including nil-slice JSON and text output.
- Successful historical typed records are revalidated from their already
  hash-checked artifact bytes. Invalid records appear failed in memory, cannot
  be reused or cited as successful evidence, and remain on disk unchanged.
  Retries consume the existing read budget. Metric summaries are recomputed
  without upgrading an existing partial or limit-reached result to complete.
- Detail output IDs must match the requested Jenkins build, GitHub run,
  Nginx Proxy Manager resource or CubeAPM trace. Hexadecimal trace IDs tolerate
  case and leading-zero normalization; explicit contradictory span trace IDs
  fail validation. Saved operation, provider, capability and command metadata
  must agree before output validation. Reuse also requires the current typed
  operation, provider and capability, even when a saved fingerprint matches.
  These local consistency checks do not authenticate owner-modified files.
- Daily briefs and session reports share the citation checks used by session
  validation. Missing or failed evidence marks the recorded claim unsupported;
  partial coverage and source-quality warnings appear beside it. Briefs qualify
  unreadable evidence without discarding the recorded finding, and reject
  foreign-org or mismatched session identities. Previous-review summaries also
  report citation warnings while preserving copied actions and follow-up duties.
- Previous reviews are chosen by their parsed finish times, with creation time
  as the fallback for older records and support for legacy UTC dates. Briefs and
  new reviews agree on ties. History sorts parsed creation times before applying
  its limit. Foreign-org sessions, malformed creation times and incident
  narratives with foreign-org or malformed metadata do not enter history.
  Session commands check org ownership even for ordinary operator reads.
  Previous summaries recheck org/environment and validate action records before
  showing their receipts; daily briefs use the same action validation and org
  checks. Copied follow-up duties survive an unavailable source.
- `sha2` is updated to 0.11.1 with explicit byte-to-hex formatting. Standard digest
  vectors and compatibility tests verify that existing hashes are unchanged.
  CI now includes a pinned cargo-audit job.
- Resolver responses must include a nonempty command, a string effect and a
  boolean credential-output flag. Malformed successful responses fail instead
  of reverting to registry checks. Legacy fallback requires an explicit first-line
  error naming the unsupported `commands` or `resolve` entry point, without a
  classification. Other failures and unknown effects are refused. Error messages
  omit child-supplied command names, effects and diagnostics that could contain
  credential values; known effect categories remain visible.
- An optional native Rust test exercises the actual resolver handling against
  all ten pinned first-party CLI binaries, with isolated config and no account
  credentials. The existing optional Python checks were also run against installed
  first-party and adopted CLIs. Their help check now uses AWS's `help` subcommand,
  verifies its manual heading, and supplies a dependency search path and disabled
  pager. Temporary test homes are cleaned, and direct resolver-test invocation
  includes the classes previously defined after `unittest.main()`.

Validation: formatting and Clippy passed; 55 Rust tests passed, including the
optional real-CLI resolver check. The Python suite ran 482 tests with all four
optional checks enabled and no skips; installer tests passed. The help check
covered 52 exact paths across installed GitHub, AWS, Kubernetes and Shopify CLIs.
Other adopted CLI binaries were not installed for this pass. Linux and
Windows cross-compilation and Clippy checks and Cargo package verification passed. A frozen
web install, type check, static build and exported-search check passed. The web
dependency audit reported no vulnerabilities.

Debug and optimized release executables both completed isolated synthetic demos
with the updated quality handling. The checkout's release executable was rebuilt.

The Rust audit scanned 156 crate dependencies against 1,349 current advisories
and reported no vulnerabilities. It does report the informational
[bincode maintenance warning](https://rustsec.org/advisories/RUSTSEC-2025-0141),
introduced transitively by the PromQL grammar libraries. Those libraries use
bincode for generated parser tables. The warning remains visible and is not
suppressed. [grmtools 0.15](https://github.com/softdevteam/grmtools/blob/master/CHANGES.md)
replaced bincode with wincode, but the current
[promql-parser 0.11.1 manifest](https://github.com/GreptimeTeam/promql-parser/blob/v0.11.1/Cargo.toml)
still requires grmtools 0.14.2. Removing the warning from this dependency graph
requires an upstream parser update or a maintained fork; this pass retains the
released parser and its full grammar checks.

Both response and legacy accounting depend on retaining the original source
records. Old histories lacking stable IDs or cumulative billing fields retain
the narrower timestamp fallback described above.

Mixed-format regression fixtures now count 4 earlier legacy tokens, a 10-token
response and 5 later legacy tokens as 19. Reset fixtures retain separate charges
on either side of valid context-window fills, including inherited zero baselines.
The optimized executable also passed all 67 spending tests.

Native execution on Linux and Windows, the new GitHub CI checks, and acceptance
against live provider accounts were not run in this local pass. No production
deployments, account settings or credentials were changed.
