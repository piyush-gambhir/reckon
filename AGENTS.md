# Agent guide (Codex / OpenCode / any non-Claude runtime)

**Read [`CLAUDE.md`](CLAUDE.md) — it is the canonical agent guide for this workspace.** Everything there applies to you regardless of runtime: the tool inventory, the Database safety contract, the RCA workflow, and the agent guidelines. This file only covers what differs outside Claude Code.

## Start here: preflight

Run `./scripts/reckon preflight` before the first query of any task. It prints the active
environment, which integrations are locally configured, which are not and why, what knowledge
exists, and any warnings — without touching infrastructure. Use `./scripts/reckon verify` when
live connectivity must be proven. `./scripts/reckon doctor` diagnoses setup problems;
`./scripts/reckon use <env>`
switches environment.

## Saved debugging workflow

Use [docs/INVESTIGATIONS.md](docs/INVESTIGATIONS.md) for the executable workflow.
For a mapped service, create a session with `scripts/reckon debug` (or
`investigate`, `monitor`, `analyze`), specifying environment, service, question,
and timezone-qualified `--from` / `--to`. Start is local unless `--collect` is
passed. Run `collect <session> --env <env>` for the supported live read operations,
then `resume <session> --json` to retrieve evidence paths and the investigation plan.

Read the source artifacts and apply the mode's methodology; the collector does
not diagnose the cause. Use `note` to record supported findings with evidence IDs
and hypotheses with next checks, `report` for a briefing, and `promote` when a
debugging session needs a full incident RCA. Sessions are private in `sessions/`;
incident outputs retain the `incidents/` convention. Missing service mappings must
be resolved from actual knowledge/discovery; do not invent aliases or metrics.

The core requires Python 3.9+ and uses no external Python packages. The offline
`scripts/reckon demo` exercises the full saved workflow without provider access.

direnv is optional. `scripts/agent.sh` activates the selected environment before launching an
agent; for direct CLI use, activate manually with `eval "$(./scripts/reckon env)"`.

## Investigation methodology

The full RCA methodology lives at [`skills/reckon/SKILL.md`](skills/reckon/SKILL.md) (with references in `skills/reckon/references/`). Claude Code loads it as a skill automatically; if your runtime has no skill loader, **read that file at the start of any incident investigation** and follow it. Consult [`infra-knowledge/`](infra-knowledge/) before querying anything.

## Things that don't transfer from Claude Code

- **Permission prompts** — Claude Code's allowlist enforces the "every DB query prompts for approval" contract. On other runtimes, use the equivalent (Codex: approval mode `untrusted`/`on-request`; never run with full-auto against this workspace), because the DB clients touch production.
- **Skills** — the `grafana`/`jenkins`/`cubeapm` command-reference skills under `.agents/skills/` are plain markdown; read them like documentation.

## Billing / keys

Subscription logins (Claude Pro/Max via `claude` → `/login`, ChatGPT via `codex login`) need no keys in this workspace. API-key billing reads `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` from `.env.<env>`. `scripts/agent.sh` activates the selected environment and launches whichever runtime is available or requested.

## Hard rules (same as CLAUDE.md, restated because they are load-bearing)

- This workspace is **single-environment-at-a-time** and **read-only by usage**: never write to a DB, never `kubectl apply/delete/scale`, never trigger a Jenkins build, never produce to Kafka — and never consume with a group id (`kcat -G` / `rpk topic consume -g`), which joins and rebalances a real consumer group.
- Re-read the **Database safety contract** in `CLAUDE.md` before any `psql`/`mysql`/`mongosh` query. `EXPLAIN` first, `LIMIT` always, and invoke mysql via `mysql --defaults-extra-file="$XDG_CONFIG_HOME/mysql/my.cnf"`.
- Write all RCA output to `incidents/<YYYY-MM-DD>-<slug>/` per [`skills/reckon/references/incidents-convention.md`](skills/reckon/references/incidents-convention.md) — never to the repo root.
