---
title: Customising infra-knowledge
description: How to populate the team-specific facts the skill consults before each investigation.
---

The skill captures a *generic* RCA methodology. The *facts* about your environment — service names, label spellings, server quirks, latent slow queries, deploy pipelines — live in the workspace's `infra-knowledge/` folder. The skill reads it before each investigation, so the more accurate this folder is, the less time the agent wastes rediscovering things you already know.

## What lives here

The repo ships seven `*.example.md` templates. Each is a starting point for one kind of fact:

| Template | Purpose |
|---|---|
| `services.example.md` | Canonical service-label values, owners, what each service does. |
| `metric-conventions.example.md` | Label names, value gotchas, which metrics to query. |
| `server-quirks.example.md` | Reverse-proxy / API quirks the CLIs work around. |
| `known-issues.example.md` | Pre-existing slow queries / fragile endpoints / latent bugs. |
| `bulk-endpoints.example.md` | Endpoints that produce sustained downstream load when called. |
| `deploy-pipelines.example.md` | Jenkins job names → service mapping for deploy correlation. |
| `oncall.example.md` | Who to escalate to. |

To customise, copy each `<topic>.example.md` to `<topic>.md` and fill in. The filled-in `*.md` files are gitignored — they reference real service names and tenant-specific data that should not be committed.

```bash
cd infra-knowledge
for f in *.example.md; do cp -n "$f" "${f%.example.md}.md"; done
$EDITOR services.md
```

## How the skill uses it

Before any cascade level runs, the skill checks `infra-knowledge/` for:

- The canonical service spelling (`MEDIA-SERVICE` vs `Media-Service` — the wrong one looks like "no data").
- Whether a metric has known label gotchas (e.g., `service.version` is `UNSET` everywhere, so don't aggregate by it).
- Whether the symptom matches a known latent issue ("`cadence_queue_video/select` runs at 200–900ms all day; if you see it slow, that's not new").
- Whether a Jenkins job name maps to the suspected service for deploy correlation.

A correctly-populated `services.md` typically saves 2–5 minutes per investigation. A correctly-populated `known-issues.md` prevents the wrong conclusion ("the DB query is suddenly slow!" → no, it's been slow all day; the volume is the new variable).

## Updating mid-investigation

The skill's [post-incident self-review](/reference/post-incident-self-review/) explicitly asks *"what workspace knowledge would have saved me time?"* If a fact surfaces during an investigation that wasn't in `infra-knowledge/` yet, the skill writes it back. Examples that have landed in real workspaces:

- A new service label (e.g., `CONVERSATIONAL_AI` uses an underscore where every other related service uses a hyphen) → `services.md`.
- A CLI quirk (e.g., `cubeapm traces services` fails against this server because the reverse proxy strips a path prefix) → `server-quirks.md`.
- A logs ingestion lag value (e.g., this team's VictoriaLogs backfills 5–20 min behind wall-clock) → `server-quirks.md`.

These accumulate over time and turn the workspace into a curated, team-specific knowledge base. The next on-call doesn't repeat the discovery work.

## Sharing back across teams

The committed `*.example.md` files are *generic*. If something belongs there — a CLI invocation pattern, a label convention that's true for any CubeAPM deployment, a query recipe that any team would benefit from — promote it from your local `*.md` to a PR against the `*.example.md`. The template improves; the next team to clone the workspace inherits the improvement.
