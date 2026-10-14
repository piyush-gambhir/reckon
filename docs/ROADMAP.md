# Reckon roadmap

Written 2026-10-14 from four research passes: a survey of comparable public
projects, a study of OpenSRE, a tool-by-tool CLI survey for a multi-business
operator, and an audit of the nine sibling CLIs. [PRODUCT-PLAN.md](PRODUCT-PLAN.md)
holds the model and the current milestone; this file is the long view.

## Where Reckon stands

| Milestone | State |
| --- | --- |
| M1 Orgs, environments, connections | Built (PR #38): every CLI through `reckon run`, per-org folders, pinned agents, sessions, migration |
| M3 Approved writes, first slice | Built (PR #39): `reckon act prepare/apply`, receipts, `github.issue.create` |
| Workflows | Growth review and release review exist as skill playbooks |

## What the research says

1. **Workflows win, not connector count.** Comparable products that last
   (Paperclip, Dust, OpenWork, HolmesGPT) are judged by repeatable work, not by
   how many tools they list. Reckon's value is a weekly review that is right and
   saves time, across tools that other products keep separate.
2. **Adopt before building.** Maintained, agent-friendly CLIs now exist for
   Google Workspace (`gog`), Stripe, Razorpay, Meta Ads, Google Ads, Sentry,
   Cloudflare (`cf`) and HubSpot (Agent CLI). Building is justified only where an
   adoption trial fails a real workflow.
3. **Scope must bind to real resources.** A credential often reaches several
   businesses. Composio pins connected accounts; ContextForge separates seeing a
   tool from using it. Reckon should record which account, repositories, sites,
   projects and namespaces each connection may touch, and check them.
4. **Evidence needs data-quality facts.** Coverage, sampling, preliminary data,
   truncation, timezone and pagination completeness belong next to raw output and
   in reports (GSC already does most of this).
5. **Writes need freshness and reconciliation.** LangGraph documents duplicate
   side effects on replay; HumanLayer's design puts approval at the tool boundary.
   Reckon has the boundary; it still needs target fingerprints, expiry and a
   read-back path for `unknown` outcomes.
6. **Memory should be files with provenance.** Graphiti's temporal validity and
   Letta's inspectable memory point to the same thing: facts with a source, a date
   and a replacement history, not an opaque vector store.
7. **Evaluate on recorded evidence.** OpenSRE removed its investigation suites in
   September, but its pattern (scenario evidence separate from a withheld answer
   key) and HolmesGPT's fixture evals are the right shape for Reckon.
8. **Skip the heavy runtime.** OpenSRE now requires an account and a hosted
   model; gateways like ToolHive and agentgateway solve team problems. Reckon's
   edge is staying local, stdlib and file-based.

## Milestones

Each milestone is reviewable on its own. Measure before widening: workflow runs
per week, minutes saved, wrong-context refusals, unsupported findings, write
outcomes.

### M2 Workflows that pay (next)

- Growth and release reviews get explicit inputs, required checks, skipped and
  failed checks, and completion judged from collected evidence, not closing prose.
- A saved review links observation, finding, proposed action and the action's
  receipt, so next week's review starts from last week's.
- Done 2026-10-14: `reckon run --cache AGE`, keyed by org/env/connection/exact
  command, stored redacted, evidence keeps the original observation time.
- Read budgets per workflow and quota warnings (Clarity allows 10 exports a day).
- Data-quality fields in evidence records for the commands the two reviews use.
- Dogfood on two real businesses for two weeks.

### M3 Approved writes, complete

- Done 2026-10-14: 24-hour expiry on prepared actions; `reckon act reconcile <id>`
  upgrades `unknown` to `applied` only on proof (a marker with the action ID in the
  created issue), never to `failed`.
- Non-secret target fingerprint on prepared actions; editing means preparing again.
- More writes, in this order: `jira.issue.create`, `gsc.sitemap.submit` (has a
  native dry run), `grafana.annotation.create`, `jenkins.job.build`.

### M4 The agent CLI contract (`agent-cli/v1`)

One contract for the nine CLIs so Reckon reads safety rules from the tools
instead of a hand-written registry:

- `<cli> commands -o json`, `commands describe <id>`, `commands resolve -- <args>`:
  offline, no credentials, using the real parser (aliases, shorthands, attached
  values, conditional effects).
- Every command declares effects (`read`, `write`, `local-write`) and an agent
  policy; credential-printing commands are `deny` even though they read. Missing
  metadata fails validation.
- Flags declare a role (`connection`, `credential`, `profile`, `config`, `tls`,
  `safety`, `local-output`, ...), arity and shorthand, so Reckon generates
  `deny_args` and value-taking flags.
- Read-only and no-input are sticky: an explicit false never removes a true.
- Writes support `--input-json - --dry-run` returning the exact planned request
  and a `plan_hash`, and `--expect-plan <hash>` refuses a different plan.
- One JSON envelope on stdout, errors with a `kind`, completeness metadata.

Started 2026-10-14: gsc v0.1.8 ships `commands resolve -- <args>` (the real parser
reports command, effect and credential output), and `reckon run` consults it before
running gsc, refusing anything that is not a plain read; older releases fall back
to the registry checks.

Rollout: gsc (reference), bwt, clarity, jira, jenkins, grafana, nginxpm, es,
cubeapm. Reckon keeps its own checks for a CLI until that CLI passes conformance
tests (catalog complete, resolver agrees with execution, false flags resisted,
previews never mutate).

Fix first, in the CLIs themselves:

- Done 2026-10-14: sticky read-only and no-input in gsc (v0.1.7, with `auth token`
  refused under read-only and marked `secret_output`, and `export` a local write);
  bwt (bing-webmaster-cli#25) and clarity (clarity-cli#16) sticky read-only;
  jira sticky no-input (jira-cli#50).
- Done 2026-10-14 as PRs: es `index settings`/`mappings` read forms allowed in
  read-only mode (es-cli#50); jenkins artifact download (jenkins-cli#42) and jira
  attachment download (jira-cli#51) refused as local writes in read-only mode.

### M5 Connectors: adopt, then build

**Adopt next** (each as a provider entry with an exact read list and scoped
credentials). Done 2026-10-14 from source, pending live tests: Stripe, Razorpay,
gog, Sentry, Cloudflare. HubSpot waits for its CLI source to be published.

| Tool | CLI | Why |
| --- | --- | --- |
| Google Workspace + GA4 + YouTube | [gog](https://github.com/openclaw/gogcli) | One Go binary with read-only mode, no-input, JSON and account routing |
| Stripe | [stripe-cli](https://github.com/stripe/stripe-cli) | Revenue, subscriptions, failed payments, disputes |
| Razorpay | [razorpay-cli](https://github.com/razorpay/razorpay-cli) | Indian payments and settlements |
| Meta Ads | `meta` ([meta-ads](https://pypi.org/project/meta-ads/)) | Paid social; `ads_read` token |
| Google Ads | [google-ads-open-cli](https://github.com/Bin-Huang/google-ads-open-cli) | Read-only reporting surface |
| Sentry | [getsentry/toolkit](https://github.com/getsentry/toolkit) | Issues and release health beside CubeAPM |
| Cloudflare | [cf](https://github.com/cloudflare/cf) | DNS, security and Workers across sites (beta: pin it) |
| HubSpot | [agent-cli](https://github.com/HubSpot/agent-cli) | Pipeline and CRM hygiene |

Next tier: Resend, Shopify (first tier for ecommerce orgs), Linear, Supabase, and
whichever accounting tool each business uses (Zoho Books, Xero, QuickBooks).

**Build only after a failed adoption trial**, in this order: WhatsApp Business
Cloud API (no good native CLI; Indian sales and support), PostHog (try the
official MCP first), Crisp (official MCP first), Plausible (small API, cheap to
own). Intercom, Mailchimp, SendGrid and Google Ads already have usable clients.

**MCP, read-only, through the same connection policy** for gaps no CLI fills
(PostHog, Crisp, Notion): curated read tools, credentials injected outside the
model, results saved through the same session path.

### M6 Connections bound to resources

First slice built 2026-10-14: `reckon connect --allow kind=pattern` for GitHub
repos, Search Console and Bing sites, Kubernetes namespaces and AWS regions,
enforced by `reckon run` and prepared writes. Still to do: account identity
checked by `verify`, and more kinds (Jira projects, Jenkins folders, Stripe
account).

- Each connection records the expected account identity (checked by
  `reckon verify`) and the resources it may touch: repositories, Search Console
  properties, Jira projects, Kubernetes namespaces, Stripe account.
- `reckon run` and prepared writes refuse targets outside that list.

### M7 Knowledge with provenance

First slice built 2026-10-14: sessions record a hash of every knowledge file they
start from, and `reckon resume` reports which changed since.

- Facts in `knowledge/` carry source (evidence ID or URL), observed date, org/env
  and a review date; superseded facts are kept and marked.
- Sessions snapshot the exact runbook and company-profile content they used, by
  hash, so a review can be reproduced.
- Scoped search over sessions, incidents and knowledge before any index.

### M8 Evaluations

- 10 to 20 recorded cases: growth drop, causal deploy, unrelated deploy, missing
  telemetry, preliminary search data, quota exhaustion, wrong-environment
  request, prompt injection inside tool output.
- Deterministic checks for identity, citations and forbidden actions; human or
  optional model review for quality. Run before and after every workflow change.

### M9 Briefings and schedules

First slice built 2026-10-14: `reckon brief [--days N] [--save]`, a local digest of
open sessions, recent findings, writes waiting or unknown, connection gaps and
stale knowledge, saved to `briefs/` for a cron or launchd job.

- `reckon brief`: a stateless daily or weekly summary per org built from the
  workflows, never from accumulated chat memory (OpenSRE moved to stateless loops
  after memory caused problems).
- Optional local scheduling through launchd or cron, producing saved sessions;
  delivery to email or chat only when the operator asks.

### Later, deliberately

Team accounts and roles, a hosted runtime, a web dashboard, MCP writes and
autonomous remediation wait until the local product is used weekly and measured.

## Risks to manage

- **Connector treadmill.** Every adopted CLI needs a read list, scoped
  credentials and tests. Add tools only when a workflow uses them.
- **Denylist whack-a-mole.** Three review rounds on `reckon run` found 48 gaps;
  M4 moves safety facts into the CLIs, where the parser lives.
- **Approval fatigue.** Keep writes few and previews exact so approval stays
  meaningful.
- **Sensitive data at rest.** CRM, support and payment reads land in sessions;
  keep them private and add reviewed export before anything is shared.
- **Vendor CLIs change.** Pin versions in the installer and re-run conformance
  tests on upgrade.
