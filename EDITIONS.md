# reckon editions

reckon is split into two editions that share one brain and differ in **who runs the investigation**.

| | **Workspace edition** | **Hosted edition** |
|---|---|---|
| **Status** | ✅ This repo. The only edition being built right now. | 🔜 Deferred — not started, not designed beyond the seam below. |
| **Who reasons** | *Your* coding agent (Claude Code / Codex / any `AGENTS.md` runtime) | A deployed agent with its own LLM loop |
| **Where it runs** | Your laptop, inside a clone you `cd` into | A server, connected to the whole infra |
| **How it's triggered** | You open the folder and ask | Alerts, chat, API, schedule |
| **Human in the loop** | Always — you approve each sensitive query | No — by design |
| **What reckon supplies** | Tools + isolated credentials + methodology + facts | The same, plus the agent runtime itself |

The important thing this split protects: **the valuable part of reckon is not the CLI plumbing — it's the
methodology and the accumulated knowledge.** Both editions must consume exactly the same brain. Everything
below exists to keep that brain portable.

---

## The shared core (edition-agnostic — both editions consume this verbatim)

These assets must never assume *how* they're being run. They are the product.

| Asset | What it carries |
|---|---|
| `skills/reckon/SKILL.md` | The router: environment rule, toolbelt, knowledge-base contract, mode selection, shared discipline |
| `skills/reckon/modes/investigate.md` | Cascade methodology, the numbered pitfalls, when-to-stop rules, RCA output contract |
| `skills/reckon/modes/monitor.md` | Sweep checklist, baseline discipline, health-report format |
| `skills/reckon/modes/analyze.md` | Capacity / cost / profiling method, total-contribution ranking |
| `skills/reckon/references/` | Cascade playbook, query recipes, RCA template, root-cause taxonomy, knowledge guide, incidents convention |
| `skills/reckon/templates/infra-knowledge/` | Seed templates (`_shared/` + `env/`) that `scripts/setup.sh` copies into a fresh clone |
| The read-only safety contracts | DB / Kafka / Kubernetes / Redis usage rules (see the seam rules below) |

### Tenant data — consumed by both editions, shipped by neither

`infra-knowledge/` and `incidents/` are **gitignored wholesale**. They hold the operator's own service
inventory, quirks, on-call rotations, incident history, and raw evidence — populated by whoever cloned
reckon, against their own infrastructure. Both editions *read* them through the same contract, but they
are never distributed, which is precisely what lets the methodology above stay tenant-generic. The
conventions and seed templates that make these directories usable live in the skill (tracked); the
contents never do.

## Workspace-edition-specific (must NOT leak into the shared core)

These encode "a human is driving a shell on a laptop." The hosted edition will need its own answer for each.

| Asset | Why it's edition-specific |
|---|---|
| `.envrc`, `.env.<env>`, `.config/<env>/` | `RECKON_ENV` resolution + direnv/`XDG_CONFIG_HOME` per-environment credential isolation — a local-shell concept. A hosted agent needs the same *separation* but through secret management, not env files. |
| `scripts/setup.sh`, `scripts/activate.ps1` | Per-machine install of the CLIs |
| `CLAUDE.md`, `AGENTS.md` | Instructions addressed to an *interactive* coding agent |
| `.claude/skills/…` symlink chain | How one specific runtime discovers the skill |
| **Approval-prompt safety** (DB contract layer 4) | Depends on a human reading each query before approving |

## Outside the split entirely

`web/` is the **project's documentation website** (Next.js + Fumadocs, static-exported to Cloudflare) — the
public front door for reckon as a whole. It is neither shared core nor edition-specific: it *documents*
whichever editions exist, so when the hosted edition lands it gets described there too rather than getting a
site of its own. Don't reason about it as an edition asset.

---

## The seam — four rules that keep the hosted edition attachable

Follow these while working on the workspace edition and the hosted edition can be built later without a
rewrite of the brain.

**1. Methodology stays runtime-agnostic.** `SKILL.md` already says it's "generic on purpose" (tenant-agnostic).
Extend that to *runtime*-agnostic: never write a rule that assumes a human will approve the next step, or that
a shell / direnv / `$XDG_CONFIG_HOME` exists. Describe *what to check and why*, not *how your terminal is set up*.

**2. Facts stay in `infra-knowledge/`, never in the skill.** Already a rule (SKILL.md §9 redaction rule). It's
what lets a hosted agent point at a different tenant's knowledge folder and work unchanged.

**3. Safety rules must name their enforcement layer.** This is the load-bearing one. The Database safety
contract in `CLAUDE.md` already does it right: it states that **layer 1 (a true read-only DB role) is the only
layer that actually denies writes across every access path**, and that the session flags and approval prompts
are defence-in-depth. Keep that labelling explicit everywhere, because **the hosted edition loses the human
approval layer entirely** — and the contract already tells you exactly which guarantees survive that loss and
which don't. Any new safety rule that silently relies on "the agent will ask first" is a rule that breaks in
edition 2 without warning.

**4. Tool access is a capability + an exact read-only command shape.** Document what a tool is *for* and the
precise safe invocation — not "run this in your shell." A hosted agent executes the same command shapes through
a different transport.

---

## Why the hosted edition is deferred (and what it will actually cost)

It is not a packaging exercise. Removing the human from the loop changes the trust model, and that is the
whole problem:

- **Safety stops being advisory.** Today, approval prompts are a real barrier — the friction *is* the mechanism
  (`CLAUDE.md` DB contract, layer 4). A server-side agent has no such friction, so every guarantee has to move
  into *enforced* credentials: genuinely read-only DB roles, broker ACLs limited to `Describe`/`Read`, read-only
  IAM, and a hard command allowlist.
- **Credentials stop being local.** `.envrc` keeping secrets in one folder on one laptop is replaced by real
  secret management, rotation, and blast-radius scoping.
- **It needs an agent runtime.** The loop, tool-calling, context budget, and report delivery that the coding
  agent gives us for free today would all have to be built or adopted — see `opensre` for what that costs
  (~457k LOC).
- **Audit becomes mandatory.** With no human witness per query, every action needs a durable trail.

None of that is needed to make the workspace edition excellent, which is why it is deferred. Where the hosted
edition eventually lives (this repo as a second surface, or its own repo consuming this core) is **deliberately
undecided** — rules 1–4 keep both options open.
