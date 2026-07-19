# Agent guide (Codex / OpenCode / any non-Claude runtime)

**Read [`CLAUDE.md`](CLAUDE.md) — it is the canonical agent guide for this workspace.** Everything there applies to you regardless of runtime: the tool inventory, the Database safety contract, the RCA workflow, and the agent guidelines. This file only covers what differs outside Claude Code.

## Investigation methodology

The full RCA methodology lives at [`skills/reckon/SKILL.md`](skills/reckon/SKILL.md) (with references in `skills/reckon/references/`). Claude Code loads it as a skill automatically; if your runtime has no skill loader, **read that file at the start of any incident investigation** and follow it. Consult [`infra-knowledge/`](infra-knowledge/) before querying anything.

## Things that don't transfer from Claude Code

- **Permission prompts** — Claude Code's allowlist enforces the "every DB query prompts for approval" contract. On other runtimes, use the equivalent (Codex: approval mode `untrusted`/`on-request`; never run with full-auto against this workspace), because the DB clients touch production.
- **Skills** — the `grafana`/`jenkins`/`cubeapm` command-reference skills under `.agents/skills/` are plain markdown; read them like documentation.

## Billing / keys

Subscription logins (Claude Pro/Max via `claude` → `/login`, ChatGPT via `codex login`) need no keys in this workspace. API-key billing reads `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` from `.env` (loaded by direnv). `scripts/agent.sh` launches whichever runtime is available or requested.

## Hard rules (same as CLAUDE.md, restated because they are load-bearing)

- This workspace is **production-only** and **read-only by usage**: never write to a DB, never `kubectl apply/delete/scale`, never trigger a Jenkins build, never produce to Kafka — and never consume with a group id (`kcat -G` / `rpk topic consume -g`), which joins and rebalances a production consumer group.
- Re-read the **Database safety contract** in `CLAUDE.md` before any `psql`/`mysql`/`mongosh` query. `EXPLAIN` first, `LIMIT` always, and invoke mysql via `mysql --defaults-extra-file="$XDG_CONFIG_HOME/mysql/my.cnf"`.
- Write all RCA output to `incidents/<YYYY-MM-DD>-<slug>/` per [`skills/reckon/references/incidents-convention.md`](skills/reckon/references/incidents-convention.md) — never to the repo root.
