# Reckon: remaining product and delivery plan

Reviewed 2026-09-08 against the workspace implementation. This replaces the earlier
proposal. Features marked implemented exist locally; live-server and coding-agent
quality acceptance remain open.

Reckon should shorten the path from an operational question to a supported
explanation, a useful next check, and a way to verify recovery. Its value comes
from connecting evidence across the tools we already have and remembering what
was learned. The first product surface remains the coding-agent workspace.

See [INVESTIGATIONS.md](INVESTIGATIONS.md) for current commands and
[READINESS-REVIEW.md](READINESS-REVIEW.md) for the isolation review and development
boundaries. Hosted execution remains deferred in [EDITIONS.md](../EDITIONS.md).

## Where we are

| Area | Implemented | Still required |
| --- | --- | --- |
| Integrations | Shared readiness registry for 16 integrations, including six custom CLIs; four custom CLIs in the default installer | Version/output contracts, verification history, optional-install experience |
| Collection | 18 typed operations across 10 providers including Git; argument validation, deadlines, output caps, cancellation | Payload/pagination semantics, source identity, overall budgets, bounded concurrency |
| Service context | Versioned environment-local mappings, aliases, provenance, frozen session context | Guided mapping, stricter field validation, stale mapping warnings, dependency joins |
| Debugging | Start, collect, resume, notes, reports, history, close | Evidence-aware next checks, hypothesis lifecycle, causal timelines |
| Incidents | Promotion to a preliminary snapshot with evidence and RCA template links | Supported explanation, impact/timeline generation, recovery and learning lifecycle |
| Monitor/analyze | Saved mode labels and existing Markdown methodologies | Distinct collection plans, suitable comparisons, sweep aggregation; no scheduler exists |
| Isolation | Environment switching, scoped collector configuration, private local state, guarded paths and checked artifact hashes | Remaining profile/auth contract tests and platform acceptance; this is not an OS sandbox |
| Evaluation | Offline cross-tool demo, non-CubeAPM metric case, deterministic regressions | Eight diagnostic scenarios, coding-agent runs, speed/correctness baseline, controlled live acceptance |
| Website | Workspace documentation and static site | Keep support claims accurate; it is not an operational dashboard |

The foundation is substantial. The missing product work is a reliable investigation
loop: establish onset and scope, connect actual deployment and dependency evidence,
test competing explanations, and validate recovery. More command wrappers alone
will not finish that loop.

## First complete workflow

For “checkout became slow after a deploy”:

1. Resolve explicit environment, service, window, available sources and baseline.
   Explain mapping ambiguity or missing access.
2. Establish the symptom independently of deployment timing: latency/error rate,
   traffic, onset, affected endpoints and telemetry coverage.
3. Build a timeline from deployment, workload, configuration and signal evidence.
   A successful CI run is not proof that its revision was deployed.
4. Resolve the deployed revision/image to source and inspect relevant changes.
   Follow a representative failing request into its dependencies.
5. Maintain explanations with supporting evidence, contradicting evidence and the
   next distinguishing query. Separate trigger from contributor.
6. Produce a supported answer, unknowns and recovery checks. Abstain when evidence
   cannot establish a cause.
7. Resume without losing reasoning; promote when an RCA is warranted. Store a
   reviewed learning and distinguish proposed fixes from verified shipped fixes.

The coding agent supplies reasoning. Reckon supplies context, typed reads,
provenance, continuity and evaluation. Retain this division for the first release.

## Build order

Each slice should be separately reviewable. There is not yet enough acceptance
evidence for a credible delivery date; estimate after the first contracts/replays.

| Order | Slice | Acceptance gate |
| --- | --- | --- |
| 1 | Collector contracts and evidence completion | Every exposed operation has success/empty/error/partial fixtures; partial output is not silently reused as complete; source provenance is stable and non-secret |
| 2 | Service/deployment/code correlation | Join metrics → build/run → actual deployed revision → code with evidence; an unrelated nearby deploy remains unconfirmed |
| 3 | Hypotheses and adaptive playbooks | Latency, errors, workload and changes choose different next reads; explanations have evidence-backed state transitions |
| 4 | Recovery and incident memory | Compare a new recovery window to the symptom/baseline; preserve coherent promotion snapshots; retrieve reviewed lessons and fix status |
| 5 | Workspace release acceptance | Fresh partial-stack onboarding, eight scenarios, measured agent runs, macOS/Linux execution, controlled live checks, accurate support matrix |
| 6 | Broader operational modes | Distinct monitor/analyze plans, appropriate comparisons, bounded multi-service sweeps and follow-up tracking using the same core |

Build the replay scenarios alongside slices 1–4. Record the before/after baseline
before changing agent-facing investigation guidance.

### 1. Finish the collection contract

- Declare each operation's parameter/output schemas, time semantics, supported CLI
  versions, pagination/result caps and retryable errors. Keep source-native output
  plus metadata; logs, traces and metric matrices have different meanings.
- Resolve `completeness=partial|limit_reached` versus `status=ok`. Preserve usable
  partial observations, but prevent automatic reuse from concealing missing
  pages/shards. Make exit/report behavior consistent.
- Record non-secret endpoint/profile identity and executable version. Define when
  configuration changes require refresh; current fingerprints cover only the task.
- Persist live verification time, source scope and permission failures. Installed,
  locally configured, live verified and supported are distinct states.
- Test saved profiles, SSO/cache behavior, Kubernetes credential plugins, and local
  Git configuration/partial clones. Private collector HOME deliberately does not
  reuse global login caches. Probe and collector configuration must converge on
  one tested contract.
- Add saved-profile secret redaction and an explicit reviewed export path. Notes,
  questions and arbitrary log fields are not currently a sanitized export.
- Add a total collection deadline and per-source concurrency limits. Retry only
  transient reads within that budget, preserving cancellation and attempt history.

Prioritize CubeAPM, Grafana, Jenkins and GitHub; cover every other exposed operation
before claiming the whole surface is supported. No new provider is required here.

### 2. Connect service, deployment and code

- Validate source fields and aliases before creating a session. Guide mapping from
  observed identifiers with provenance and review of ambiguity. Never invent
  metric names or silently replace curated mappings during discovery.
- Add deployment observations linking environment, service/workload, rollout time,
  build/run, artifact/image and revision, with evidence for each join.
- Normalize event times into a timeline while retaining requested/observed times,
  ingestion lag and limits of current-only snapshots.
- Establish actual deployment attribution before treating a revision as a causal
  candidate. Handle absent checkout/revision and ambiguous deployment outcomes.
- Derive remaining gaps from evidence. The current plan can still say code is
  missing after a manual `git.show`, because it only inspects the mapping.

Acceptance cases: causal deploy, failed/non-deploy CI run, unrelated nearby deploy,
and a known deployment with unavailable local source.

### 3. Make the investigation loop useful

- Add hypotheses with stable IDs, state, supporting/contradicting evidence, next
  checks and transition history. Keep existing notes readable.
- Select the smallest useful next read. Establish signal presence before accepting
  zero errors; compare rates and volume; follow a trace/dependency before opening
  broad database queries.
- Give latency, errors, workloads and changes distinct plans. Currently playbooks
  change guidance while mapped sources determine the same collection fan-out.
- Separate observation, inference, confirmed mechanism and unknowns in reports.
  An evidence reference proves provenance, not that the prose is true.
- Retain investigator control, explicit live reads, rejected explanations and
  inconclusive outcomes.

Acceptance cases include traffic growth exposing an existing bottleneck, downstream
failure, lagged/missing telemetry and insufficient access. No confirmed causal claim
is acceptable in the unrelated-deploy negative control.

### 4. Recovery and accumulated knowledge

- Add follow-up windows without rewriting the original incident window or
  silently presenting stored evidence as current health.
- Separate diagnosis from recovery. `close --outcome diagnosed` currently requires
  only a recorded finding; it is not a recovery test.
- Compose preliminary impact, timeline, explanation and open questions from the
  session; retain `RCA.md` as the reviewed incident narrative.
- Track learnings/actions through reviewed, implemented and verified states with
  sources. Jira/GitHub reads provide context; posting remains separately authorized.
- Retrieve prior sessions/incidents with explicit environment, age and provenance.
  Start with structured filters; add a rebuildable index only when measured corpus
  size or latency warrants it.

## Reuse the existing CLIs

| Need | Reuse | Reckon's responsibility |
| --- | --- | --- |
| Signals and annotations | `cubeapm`, `grafana`, `es`, `aws` | Selection, bounds, baseline, coverage and provenance |
| Deployment/source changes | `jenkins`, `gh`, local `git`/`rg` | Attribution, timeline joins, causal checks |
| Workload context | `kubectl`, `aws` | Narrow resource scope and historical limits |
| Prior issues | `jira`, `gh`, local RCA corpus | Recurrence, reviewed knowledge and verified fix status |
| Routing/certificates | `nginxpm` | Current configuration evidence with temporal limits |
| Queues/caches/databases | `kcat`, `rpk`, `redis-cli`, `psql`, `mysql`, `mongosh`, `clickhouse` | Later targeted recipes under existing role/query-approval contracts |

Keep provider API/auth/pagination implementation in the CLIs where possible.
Reckon owns bounded operations and cross-tool investigation. Jira and Nginx Proxy
Manager are already optional integrations, not default installer entries. Add pins
only after their release contracts have been validated.

## Architecture and isolation

Retain the Python 3.9+ standard-library core; there is no demonstrated reason to
rewrite it in Go. Bash/PowerShell remain activation/setup/entrypoint layers.

- `environment.py`: environment, profiles, readiness and subprocess configuration.
- `operations.py`: capability contracts, arguments and plans. Split adapters by
  provider as contracts grow; keep them out of the reasoning/session layer.
- `process.py`: bounded execution, cancellation and process cleanup.
- `store.py`: versioned private storage, atomic writes, locks and integrity checks.
- `investigation.py`: orchestration. Extract report/history functions when their
  next feature warrants it; avoid a framework refactor before acceptance.
- `skills/reckon/`: investigation methodology and provider recipes.
- `tests/` and `examples/`: synthetic fixtures only; never copied tenant data.
- `web/`: documentation, with no collector imports or tenant state.

One session has one environment and frozen context. Path scoping does not prevent
a program running as the same user from opening other files. Read-only server roles
remain the authorization boundary. Hosted execution needs separately enforced
tenant/process/network isolation, secrets, durable jobs and audit controls.

## Release gates and measurement

Create eight sanitized scenarios: causal deploy, unrelated nearby deploy, traffic
growth against an existing bottleneck, downstream failure, missing telemetry,
lagged telemetry, configuration/proxy issue, and insufficient access. Include a
non-CubeAPM stack and a minimal partial stack.

Keep these evidence types separate:

1. Deterministic tests: contracts, bounds, partial results, environment/path
   isolation, integrity, interruption/resume and command lifecycle.
2. Coding-agent evaluations: frozen evidence, hidden reference conclusions,
   recorded runtime/model, repeated runs and human-reviewed scoring.
3. Live acceptance: explicitly selected environment/service, narrow reads, known
   role constraints, tool/server versions and observed limitations.

Measure time to first useful evidence, time to supported diagnosis, query count,
user interventions, evidence validity and unsupported claims. Compare against the
current workspace on identical cases. A 30% lower median diagnosis time is a
proposed target, not a measured result or promise.

Hard gates: no cross-environment credential/evidence leakage in fixtures; bounded,
cancellable collection; valid evidence for top-level factual claims; abstention
with insufficient evidence; no confirmed cause in the negative control. Platform
claims need actual execution. Native Windows remains experimental until native
activation, process-tree cleanup and storage tests pass.

## Deferred deliberately

Unattended monitoring, alert/chat-triggered execution, a hosted dashboard, another
LLM runtime, provider marketplace, vector/graph databases and autonomous remediation
follow demonstrated workspace usefulness. Preserve the contracts that enable them
without making them prerequisites for the workspace release.
