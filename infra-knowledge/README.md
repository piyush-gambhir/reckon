# infra-knowledge

Team-owned, team-maintained facts about **your organisation's infrastructure** that the `rca-assist` skill consults during investigations. The skill itself is generic; this folder is what makes its answers specific and correct.

## Getting started

Each topic has a committed `<topic>.example.md` file that ships with the product and a `<topic>.md` file that holds your team's actual content. The `*.md` files are gitignored on purpose — they contain environment-specific details (service names, server quirks, slow queries, on-call contacts) that shouldn't be shared across tenants.

```bash
# One-time setup per workspace clone:
cd infra-knowledge
for f in *.example.md; do cp -n "$f" "${f/.example/}"; done
# ...then edit each <topic>.md with your team's real data.
```

Diff against the example file periodically to catch new sections added upstream.

## How the skill uses this folder

When `rca-assist` starts an investigation, it tries to read (at minimum) the files below. If a file is missing, the skill continues without it and notes the gap in the RCA — but every file you fill in saves real investigation time on the next incident.

| File | Purpose |
|------|---------|
| [services.md](services.md) | Service inventory: canonical names, what each owns, label spellings |
| [metric-conventions.md](metric-conventions.md) | Label conventions, metric names, time zone, expected env values |
| [server-quirks.md](server-quirks.md) | Reverse-proxy and API quirks the CLIs have to work around |
| [known-issues.md](known-issues.md) | Queries / endpoints known to be slow or fragile at baseline |
| [bulk-endpoints.md](bulk-endpoints.md) | Admin / bulk endpoints that commonly cause cascade incidents |
| [deploy-pipelines.md](deploy-pipelines.md) | Which Jenkins job deploys which service, and how to check |
| [oncall.md](oncall.md) | Who to escalate to for each service / domain |

## How to maintain it

- **Every RCA should leave this folder a little fatter.** If the RCA surfaced a latent slow query, add it to `known-issues.md`. If a service label turned out to be cased differently than you expected, add a note to `metric-conventions.md`. If the team discovered a reverse-proxy path gotcha, log it in `server-quirks.md`.
- **Keep the entries falsifiable and dated.** `cadence_queue_video/select ran 500–900ms on 2026-04-19` is useful; "this query is slow" is not.
- **Prefer pointers over prose.** Link to the RCA that first surfaced a fact instead of restating the full story.
- **Delete confidently when things change.** A stale entry is worse than no entry.

## What lives here vs. in the skill

- The **skill** (`.agents/skills/rca-assist/`) describes *how* to investigate — the cascade method, queries by pattern, document structure.
- This **folder** describes *what is true* about this environment — service names, label conventions, deploy jobs, on-call rota.

If you find yourself wanting to hard-code a service name into the skill itself, that's a sign it should live here instead.
