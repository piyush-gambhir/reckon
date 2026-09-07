# Reckon product and delivery plan

Drafted 2026-09-08 from the local Reckon repository, six sibling CLI repositories, and the existing incident conventions. This is a proposed build plan, not a claim that the features below already exist.

## Implementation checkpoint — 2026-09-08

The first workspace implementation is now in `scripts/reckonlib/`, exposed through
the existing `scripts/reckon` and PowerShell entrypoints. See
[INVESTIGATIONS.md](INVESTIGATIONS.md) for the shipped command surface and limitations.

Implemented: Bash/Zsh credential-switch isolation fixes, fresh provider-scoped
child environments, a shared readiness registry, bounded process execution,
versioned service mappings, saved sessions, 18 typed collection operations,
evidence provenance, findings/hypotheses, reports, history, resume, preliminary
incident promotion, and an offline cross-tool demo. Collection is sequential in
this first implementation. Jira/NPM are optional integrations, not additional
default setup installs.

The deterministic workflow has offline acceptance tests including a non-CubeAPM
metric source. This does not complete M3's agent-quality/speed evaluation or
live-server acceptance. Windows execution, persisted verification history,
parallel collection, and the wider operations/hosted milestones remain open.
The roadmap below is retained to show those remaining gates.

## Product outcome

Reckon helps an engineer move from a question about their systems to an evidence-backed explanation and a useful next action. It combines existing provider CLIs, our custom CLIs, source code, infrastructure knowledge, and prior investigations.

The first release should make these questions materially faster to answer:

- Why did this request, endpoint, job, or service fail or slow down?
- What changed before the problem started, and does that change explain it?
- Which upstream or downstream dependency is responsible?
- Is the problem still happening, and how can a human validate recovery?
- Have we seen this before, and was the earlier recommended fix implemented?

Health sweeps and capacity/performance analysis already have methodology in the repo. Keep them usable, then give them the same evidence and session support after the debugging workflow is proven.

## Starting point

Already present: three investigation modes, causal drill-down methodology, knowledge templates, nine private incident folders in this clone, fourteen integration entries, setup/activation/diagnostics scripts, coding-agent launchers, and a documentation website.

The current implementation relies heavily on the agent following Markdown instructions. Service mapping, query execution, evidence capture, and investigation continuity are largely manual. The main investigation recipe is CubeAPM-specific even though the intended methodology is broader.

There is an existing uncommitted hardening pass. Preserve it, review it, and finish it as part of the foundation milestone. Do not restart the repository or rewrite the sibling CLIs.

Planning assumption, open to user correction: the first-use surface is the existing coding-agent workspace. The data and capability contracts below also support a later standalone terminal or hosted runtime. This draft does not supersede EDITIONS.md's deferral of hosted execution.

## User experience

An engineer asks: “Checkout latency increased after the last deploy. Investigate staging from 14:00 to 14:30 UTC.”

1. Reckon confirms the environment, affected service, time window, and available signals. It retrieves relevant service mappings and previous findings.
2. It produces a short first assessment: whether the symptom is visible, its approximate start, affected scope, and the strongest next checks.
3. It compares symptom metrics with a baseline, attributes the change to endpoints/dependencies, and checks the actual deployed revision and relevant configuration history.
4. It maintains competing explanations with supporting evidence, contradicting evidence, and the next discriminating query. Temporal correlation alone does not confirm a cause.
5. It saves each useful observation as it works. Missing access, expired telemetry, failed queries, and inconclusive checks remain explicit.
6. It returns a concise answer with evidence links, confirmed and unresolved claims, recovery checks, and ranked next actions. A full RCA is generated when the task warrants one.
7. A later session can resume from the evidence and open questions. Rechecking live conditions is explicit; stored evidence is never presented as a current reading.

Routine debugging should not require completing a long RCA template. A short investigation can be promoted into an incident while preserving its evidence and reasoning history.

## Capability map

| Investigation need | Existing tools to reuse | Work inside Reckon |
| --- | --- | --- |
| Metrics, traces, logs, alert state | `cubeapm`, `grafana`, `es`, `aws` | Discover available signals, select source-specific recipes, normalize time and provenance |
| Deployment and code changes | `jenkins`, `gh`, local `git` and `rg` | Resolve service to repository/job/workload; establish deployed revision before inspecting code |
| Workload and cloud health | `kubectl`, `aws` | Bounded workload/event/resource reads; correlate with symptom window |
| Queues and caches | `kcat`, `rpk`, `redis-cli` | Lag and saturation recipes, source constraints, explicit unsupported signals |
| Datastore investigation | `psql`, `mysql`, `mongosh`, `clickhouse client` | Narrow diagnosis after telemetry points to a datastore; preserve query approval and role requirements |
| Earlier incidents and unresolved fixes | Local corpus, `jira`, `gh` | Add Jira integration; retrieve related tickets and distinguish proposed fixes from verified shipped fixes |
| Proxy and certificate context | `nginxpm` | Add integration for configured NPM deployments; read routing/certificate/audit information the server actually exposes |

Jira and Nginx Proxy Manager exist as sibling CLIs but are not in Reckon's current integration registry or installer release table. Validate their installed command/auth contracts before adding pins and probes.

Support providers through capabilities such as `metrics.range`, `logs.search`, `trace.get`, `deployments.list`, and `workload.events`. A workflow asks for a capability, then uses an available implementation. It must work with partial coverage; all integrations are never an onboarding prerequisite.

Do not implement every command of every CLI inside Reckon. Start with the bounded reads required by the release workflows. Extend an existing CLI only when a demonstrated API or output gap blocks a real investigation.

## Architecture

```text
Engineer question / alert text
             |
      Investigation method
      frame -> collect -> test explanations -> conclude
             |
      Local investigation services
      service resolver | capability registry | execution and evidence | session history
             |
      Existing custom and provider CLIs + local source repositories
             |
      Environment-scoped infrastructure
```

The coding agent can supply reasoning and conversation in the first release. Reckon supplies deterministic support for environment handling, collection, provenance, retrieval, and continuity. A separate LLM loop is unnecessary for that release.

### Service context

Introduce a small, versioned, tenant-local service mapping format alongside the existing Markdown knowledge. Fields cover service identity, aliases by tool, environment, repository, deploy job/workflow, workload identifiers, telemetry sources, dependency references, and owner references.

Every discovered mapping has its source and observation time. Ambiguous aliases require resolution; discovery cannot silently overwrite curated mappings. A current dependency graph is an observation, not proof of the historical incident topology.

Keep knowledge prose for explanations and quirks. Structured mappings handle exact joins across tools. Do not build a graph database for the first release.

### Integration contract

For each supported operation, declare: capability, supported binary/version range, required configuration, argument/input schema, expected output form, time units, environment variables, timeout, pagination/size limits, and relevant read-only requirements.

The registry distinguishes installed, locally configured, live verified, and unsupported. Live verification records its time and scope. A successful ping does not prove permission to read every resource or establish a read-only role.

Select one tested registry source for Bash and PowerShell rather than maintaining separate lists. Migrate incrementally while keeping current entrypoints working.

### Execution and evidence

Use argument arrays and operation-specific validation for supported collection commands. Construct a fresh child-process environment from the selected environment's configuration and a documented base-variable allowlist. Test removal of inherited credentials and stale derived aliases.

Enforce deadline, output limit, pagination limit, cancellation, and bounded concurrency per source. Retry only appropriate transient failures within the same budget. Explicit partial results are preferable to silently truncated success.

Each evidence record includes an ID, session/environment, source and operation, redacted invocation/query, requested time window, observation time, exit status, completeness/error information, and artifact reference. Preserve source-native output and add normalized metadata; do not flatten logs, metrics, traces, and deployment events into a lossy common payload.

Capture artifacts with restrictive permissions. Redact credentials from command metadata and default exports; raw logs may contain tenant/customer data and remain local by default. Treat tool output as evidence, never as instructions authorizing another action.

This execution layer governs commands routed through it. It cannot prevent a general-purpose coding agent from using another shell or network path. Server-side permissions remain the infrastructure authorization boundary; existing sensitive-query approval requirements still apply.

### Sessions and history

Add tenant-local sessions for short debugging, sweeps, and analysis, with an explicit promotion path into the existing `incidents/<date>-<slug>/` convention. New session directories must be gitignored before any real data is written.

Session metadata stores environment, question, scope, status, evidence index, and open checks. Preserve RCA.md as the authoritative incident narrative. Avoid maintaining separate contradictory copies of the diagnosis.

Support resume, report, and search. Begin with exact service/error/category/time filters over local files; add a rebuildable local index only if corpus size warrants it. Retrieval must show provenance and distinguish old recommendations from verified outcomes.

### Playbooks

Separate the provider-independent investigation method from CubeAPM-specific query recipes. Initial playbooks cover latency regression, error spike, failed request/job, workload failure or missing telemetry, and change correlation. Queue and database diagnosis remain targeted branches of those workflows.

Collect independent baseline/context reads concurrently within source budgets, then let findings determine deeper queries. Do not fan out across every system for every question.

## Delivery milestones

| Milestone | Deliverable | Acceptance gate |
| --- | --- | --- |
| M0: trustworthy foundation | Finish pending hardening; fix environment carryover; enforce probe timeouts; registry parity; regressions | Fresh activation, same-shell switching, missing configuration, aliases, timeout/cancellation, and launch behavior pass isolated tests; existing website checks remain green |
| M1: one complete debugging workflow | Service mapping; session creation; bounded collection with provenance; latency/error playbook using configured observability plus deploy/code context | A synthetic cross-tool incident yields a useful short answer with evidence and open questions; interruption/resume works without losing evidence; no database access required |
| M2: complete incident workflow | Additional failure playbooks; hypothesis tracking; promote to RCA; recovery verification; prior-incident retrieval; optional Jira/NPM reads | Cross-tool cases distinguish trigger from contributor, wrong deploy from causal deploy, missing telemetry from health, and old evidence from current state |
| M3: releasable workspace product | Partial-stack onboarding; documented commands; sanitized demo; evaluation suite; supported-platform checks | A fresh clone with one observability source can complete the documented workflow; all release gates below pass |
| M4: broader operations | Durable monitor/analyze sessions; deploy comparison; richer recurrence and follow-up tracking | Extend evidence/session contracts without separate workflow implementations; evaluate false alarms and usefulness on representative tasks |
| M5: team/hosted surface | Decide trigger/delivery surface, runtime, secrets, tenant isolation, durable jobs, audit and operational budgets | Separate design and explicit scope decision; same core playbooks/evidence contract; enforced permissions and isolation tested before unattended infrastructure access |

M0 and the first M1 workflow are the next implementation scope. M3 defines the first complete workspace release. M4/M5 describe expansion, not prerequisites for shipping it.

## Release evidence

Create at least eight sanitized replay cases covering: a causal deploy, an unrelated nearby deploy, increased request volume against an existing bottleneck, a downstream failure, missing telemetry, lagged telemetry, a configuration/proxy issue, and insufficient access/evidence.

Separate deterministic tests of collectors/session handling from agent evaluations. For agent evaluations, freeze available evidence, keep reference answers hidden from the run, pin the evaluated runtime/model configuration, repeat runs, and score findings against a human-reviewed rubric. Live collection is a separate acceptance exercise against explicitly chosen infrastructure.

Track time to first useful evidence, time to supported diagnosis, number of queries, user interventions, evidence-link validity, and unsupported top-level claims. Compare against the current workspace on the same cases. Proposed performance target: at least 30% lower median time to supported diagnosis without a drop in correctness; establish the baseline before treating this as a release promise.

Hard gates: no cross-environment evidence/credential leakage in the test suite; bounded/cancellable collection; every top-level factual claim cites evidence; explicit abstention in insufficient-evidence cases; zero confirmed causal claims in the negative-control case. Mock/replay success is not certification against every production server.

Platform claims require actual execution on the claimed platforms. Native Windows stays experimental until activation, process control, and evidence handling pass Windows CI. Do not block the first macOS/Linux workflow on complete native Windows parity.

## Implementation decisions and boundaries

- Retain the current Bash/PowerShell bootstrap and launcher entrypoints. Choose the smallest maintainable implementation for structured local services at M1; a Go helper is a reasonable candidate because the sibling CLIs already use Go. Do not commit to a rewrite merely for consistency.
- Proposed product verbs are `debug`, `investigate`, `resume`, `report`, `history`, `monitor`, and `analyze`. Final command syntax follows the first-use surface decision; none of these are claimed to exist today.
- Prioritize the configured Grafana/CubeAPM/Jenkins/GitHub workflow, then a non-CubeAPM fixture to prove capability portability. Add Jira and NPM where they close a demonstrated context gap.
- Mitigation suggestions and ticket/message drafts belong in outputs. Executing remediation, posting messages, or creating tickets is a separate explicitly authorized action, not an automatic consequence of investigating.
- Avoid an early hosted dashboard, broad provider marketplace, custom model runtime, vector database, or autonomous remediation. Revisit each when measured user needs justify the additional system.

## Next implementation checklist

- [x] Proceed with the existing coding-agent workspace as the first-use surface.
- [x] Add isolated regression cases and fixes for credential carryover and timeout gaps. Native Windows execution remains unverified.
- [ ] Select a sanitized latency/error scenario and record the current workflow baseline.
- [x] Specify service mapping, session metadata, operation, and evidence-record fixtures for the synthetic scenario.
- [x] Build the deterministic M1 path: resolve -> collect -> investigator notes -> report -> resume. The coding agent supplies the explanation.
- [ ] Run the scenario with existing coding agents; inspect actual query count, latency, correctness, and user effort.
- [ ] Use those results to size M2; keep a visible backlog with acceptance criteria rather than adding integrations opportunistically.
