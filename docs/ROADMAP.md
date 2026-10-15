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

- Done 2026-10-14: growth and release reviews are recipes (`skills/reckon/workflows/*.json`,
  overridable per org) with parameters and required checks; `reckon check` marks each met
  by evidence, skipped with a reason, unavailable or to do, and `close --outcome done`
  is refused while any is to do.
- Done 2026-10-15: a review links observation, finding, proposed action and the action's
  receipt, so next week's review starts from last week's: runs of a recipe form a
  series (`series` in the recipe, the site for growth reviews), a new run shows the
  last finished one's findings, actions and session-linked writes (`reckon act prepare
  --session`), and its `follow-up` check needs a `reckon note --follows` finding for
  each earlier action.
- Done 2026-10-14: `reckon run --cache AGE`, keyed by org/env/connection/exact
  command, stored redacted, evidence keeps the original observation time.
- Done 2026-10-14: per-session read budgets (`reckon task --budget N`), enforced
  before any read runs.
- Done 2026-10-14: daily quota ledger per connection (`quota` in providers.json; Clarity's
  10 Export API requests), warning at two left and refusing when spent; cache hits are free.
- Data-quality fields in evidence records for the commands the two reviews use.
- Dogfood on two real businesses for two weeks.

### M3 Approved writes, complete

- Done 2026-10-14: 24-hour expiry on prepared actions; `reckon act reconcile <id>`
  upgrades `unknown` to `applied` only on proof (a marker with the action ID in the
  created issue), never to `failed`.
- Done 2026-10-15: non-secret target fingerprint on prepared actions (the connection
  as configured plus settings such as `GH_HOST`); any change means preparing again.
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
to the registry checks. All nine CLIs now have the resolver (gsc released; PRs bing-webmaster-cli#25,
clarity-cli#16, es-cli#51, jenkins-cli#43, jira-cli#52, grafana-cli#43,
nginxpm-cli#46, cubeapm-cli#43), and Reckon consults it for each. Next: complete
local-write annotations (login, config, downloads) so the resolver alone is enough,
then flag roles, so `deny_args` can be generated.

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

Checked 2026-10-14 against real release binaries (no account): every allowed command
of all nine adopted CLIs exists: Stripe 1.53.1, Linear 3.0.0, Supabase, Shopify 4.7.1,
gog 0.44.0, Sentry 0.45.0, Resend 2.23.0, Razorpay 1.0.9 and cf 1.0.0-beta.16
(`RECKON_ADOPTED_BIN` test, with a made-up command as a control for each). Still to
run against real accounts.

Done 2026-10-14 from source, pending live tests: Linear (schpet/linear-cli),
Resend, Shopify (`store execute`, which refuses mutations; forced off) and Supabase
(Management API reads only: `inspect db` and `migration list` create a read-write
login role on linked projects, so database inspection goes through a `postgres`
connection with a read-only role).

**Next wave**, from a GitHub survey on 2026-10-14 (stars, releases and commits
fetched, not guessed; ten of the rows were re-checked against the GitHub API the
same day and matched). Adopt in this order, each only when a business uses it:

| Order | Tool | CLI | Why this one |
| --- | --- | --- | --- |
| 1 | Meta Ads | `meta` (official, PyPI meta-ads) | `ads_read` system-user token is a hard read-only boundary |
| 2 | App Store Connect | [asc](https://github.com/rorkai/App-Store-Connect-CLI) (7.8k stars, Go) | Sales, analytics, reviews, TestFlight; JSON output |
| 3 | Chargebee | [chargebee/cli](https://github.com/chargebee/cli) (official) | Env auth, JSON, live-site writes blocked by the CLI |
| 4 | Railway, Render, Fly | official CLIs | Hosting status and deploys; env tokens, JSON |
| 5 | PostHog | `posthog-cli` (official, in the PostHog monorepo) | HogQL `query` and flags; replaces the planned MCP or build path |
| 6 | Google Ads | [google-ads-open-cli](https://github.com/Bin-Huang/google-ads-open-cli) | Read-only by design; pin a commit (no releases) |
| 7 | Chatwoot | [chatwoot/cli](https://github.com/chatwoot/cli) (official, Go) | Support inbox reads; allowlist, since it can reply |
| 8 | Neon, PlanetScale, Upstash | official CLIs | Hosted database status with scoped service tokens |
| 9 | Google Play | [gplay](https://github.com/tamtom/play-console-cli) | Official APIs only, JSON default; trial first |
| 10 | Slack | [slackcli](https://github.com/shaharia-lab/slackcli) | Search and history with a read-scoped bot token only |

Trial before adopting (official but alpha, closed or quiet): HubSpot agent-cli,
Xero, QuickBooks (`intuit-cli`), Zoho Books (`zb`, community), Loops, Mailchimp
(`mcapi`), Freshdesk, UptimeRobot, Checkly, Vercel, Netlify, Turso, Lemon Squeezy,
Paddle. Skip: GST portal (needs a licensed intermediary; read GST from Zoho Books
or Tally), Mixpanel and Amplitude (no CLI, no current user), Discord (only user-token
CLIs exist, against its terms).


**Build our own** where GitHub has no maintained CLI and the read API is small, in
this order (each a Go CLI with `commands resolve` from day one):

1. WhatsApp Business Cloud API: Meta archived its SDKs; phone numbers, templates,
   conversation and pricing analytics, quality rating. Indian sales and support.
   First version built 2026-10-14 as `whatsapp-cli` (local, not yet published):
   GET-only, `commands resolve`, Graph v25.0, reviewed; not yet run against a live
   account. Conversation analytics left out (that pricing ended 2025-07-01).
2. Zoho CRM: no CLI anywhere; records, search and COQL share Zoho Books' OAuth.
3. Tally (TallyPrime): XML over a local HTTP port; ledgers, vouchers, trial balance.
4. Plausible: one Stats API v2 endpoint and a sites list; a one-day build.
5. WooCommerce: REST `/wc/v3` with read-only consumer keys, so no shell on each host.
6. Zendesk Support: the official `zcli` covers apps and themes, not tickets.
7. Better Stack, Crisp, Brevo, Porkbun, Attio: small APIs, built when a business uses one.

**MCP, read-only, through the same connection policy** for gaps no CLI fills
(PostHog, Crisp, Notion): curated read tools, credentials injected outside the
model, results saved through the same session path.

### M6 Connections bound to resources

First slice built 2026-10-14: `reckon connect --allow kind=pattern` for GitHub
repos, Search Console and Bing sites, Kubernetes namespaces and AWS regions,
enforced by `reckon run` and prepared writes. Done 2026-10-15: `reckon connect
--account` pins the login, email or account ID a connection must use; `reckon verify`
reads it from the live check (GitHub, AWS, Jira, Jenkins, Grafana, Nginx Proxy
Manager, Elasticsearch, Search Console, WhatsApp) and reports `wrong_account`, and
`reckon act apply` checks it before every write. Still to do: identity for the
adopted business CLIs once their output is seen on a live account, and more kinds
(Jira projects, Jenkins folders, Stripe account).

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

First slice built 2026-10-14: `reckon check <session>` runs the deterministic part
(evidence integrity, findings citing successful evidence, partial evidence,
hypotheses without next checks, foreign evidence, closed without a finding,
knowledge changed since the start).

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
- Done 2026-10-14: local scheduling through launchd or cron (`reckon routine`), producing
  saved sessions; delivery to email or chat only when the operator asks.

### Features found in comparable projects

From a survey of Paperclip, OpenHands, Goose, Composio, OpenWork, OpenSRE,
HumanLayer, Steampipe, agentgateway, ContextForge, HolmesGPT, OneCLI, Agent Vault
and ToolHive (2026-10-14), ranked by value for one person running several businesses:

| Feature | Seen in | Effort |
| --- | --- | --- |
| Done 2026-10-14: cross-org brief (`reckon brief --all-orgs`), one morning view of every business | Steampipe aggregators, Paperclip | S |
| Done 2026-10-14: activity log of every run, refusal, prepare and apply (`reckon log`) | Paperclip, Agent Vault | S |
| Done 2026-10-14: secret references (`keychain:`, `op://`) resolved only inside `reckon run` | ToolHive | S |
| Done 2026-10-14: routines (`reckon routine add/install/run`): launchd or cron runs a pinned headless agent on a workflow; overlapping runs skip | Paperclip, HolmesGPT, Goose | M |
| Done 2026-10-14: workflow recipes with parameters, required checks and report rules (evidence-backed `action` notes, a cap per review) | Goose recipes | M |
| Rejected 2026-10-15: large output spilled to the session with a preview. `reckon run` output is often piped (`\| jq`), and an agent's Bash tool is a pipe too, so truncating would break pipelines; Claude Code and Codex already spill long output themselves, and `--session` keeps the full output as evidence | HolmesGPT | S |
| Done 2026-10-14: agent spend per org from Claude Code and Codex session files (`reckon spend`; prices from `~/.reckon/prices.json`), with a per-org token budget warned in `reckon open` and the brief | Paperclip | M |
| Done 2026-10-14: goals per org (`knowledge/*/goals.json`) bound to reads, reported in briefs from the newest saved evidence | Paperclip | M |
| Per-connection circuit breaker and quota ledger | ContextForge | S |
| Done 2026-10-14: polled watches and an inbox (`reckon watch`, `reckon inbox`, brief section); first poll is a baseline | Composio triggers | M |
| Done 2026-10-14 as `reckon org add <new> --like <org>`: environments and connection layout, never secrets or resource limits | Paperclip companies spec | S |

Rejected for now: an HTTP credential proxy (Agent Vault, OneCLI) needs a TLS
intercepting CA and a separate machine to mean anything.

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
