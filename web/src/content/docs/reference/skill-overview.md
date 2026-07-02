---
title: The rca-assist skill
description: The cascade methodology, the pitfalls, and the post-incident self-review — what the skill actually does.
---

The skill at `skills/rca-assist/SKILL.md` is the methodology the agent follows. It is intentionally generic: it assumes only that you have CubeAPM-style metrics/traces/logs, Grafana-style dashboards/annotations/alerts, and Jenkins-style builds. Anything team-specific lives in `infra-knowledge/`, not the skill.

## The cascade

Every investigation walks down a six-level chain. At each level, the agent answers two questions: *where did the time/errors go* and *what fed this level with load or bad inputs*. It descends only when it has a quantitative attribution.

1. **Frame.** Service label, UTC window, symptom-as-a-number.
2. **Service-wide signal.** Apdex / p99 / error-rate over the window + 30-min buffer.
3. **Endpoint attribution.** `topk` by `root_name` for the bad signal.
4. **Per-call latency vs call volume.** The hinge step — distinguishes "service got slow" from "service got busy". The fix is completely different in each case.
5. **Sub-span / downstream attribution.** Where the time went inside the endpoint.
6. **Trigger.** Deploy, caller spike, scheduled job, bulk action, upstream outage.

Detailed checklist: see `skills/rca-assist/references/cascade-playbook.md` in the repo.

## The pitfalls (§6 of SKILL.md)

The skill maintains a list of failure modes that have actually bitten real investigations. Each is a *defense* against a known mistake:

1. **Did per-call latency actually change, or just call volume?** A slow endpoint might be slow all day.
2. **Are you looking at the right service name?** Labels can be inconsistent across an inventory.
3. **Is the trace sampler hiding the real root span?** Don't conclude "no such call" from an empty trace search.
4. **Is the alert's tolerance threshold realistic?** Apdex 0.2 means *every* request was frustrated.
5. **Distinguish cause from effect.** Every downstream spike looks "caused" by the next level down — but the real cause might be a lateral caller change.
6. **Don't trust fresh log buckets for recovery determination.** Logs ingestion can lag several minutes; cross-check with the equivalent metric or re-query after one ingestion window.
7. **For `/internal/` or service-to-service endpoints, identify the caller before writing the RCA.** A failing endpoint with `userAgent=node` and an RFC1918 `context.ip` has *some* internal owner; finding it (or proving it's not APM-instrumented) is part of the RCA.
8. **Recognise the alert source before chasing the alert rule.** Not every notification is a Grafana alert. CubeAPM-native alerting, application-emitted alerts, and PagerDuty composite policies are dead ends if you `grafana alert rule list` for them.
9. **Don't anchor on the previous RCA for the same endpoint.** A fan-out endpoint can hit a different dominant downstream each time it's stressed — recompute the sub-span attribution from scratch.
10. **Discover the deployed branch from Jenkins**, don't assume `git log <default-branch>` is what production runs.
11. **Inspect deploy *cause* and *params***, not just timestamp + result — a manual "override" deploy is a load-bearing signal.
12. **Record negative findings** ("tested, falsified") instead of silently dropping the question.
13. **Downgrade, don't delete** a root cause that the data didn't support — keep the reasoning.
14. **Enumerate log datasources** (e.g. short-retention CubeAPM vs long-retention Loki) before assuming the obvious one covers your window.
15. **Read a producer endpoint's branching switch** before assuming the default path fired.

This list grows: any time the agent corrects a conclusion mid-investigation, the rule that would have prevented the mistake gets back-ported here (pitfalls #6–#15 all came from real incidents). The canonical, fully-worded list is §6 of `skills/rca-assist/SKILL.md`.

## The post-incident self-review (§9 of SKILL.md)

After every RCA, the agent runs a four-question retrospective:

1. Did I correct any conclusion mid-investigation? Why?
2. What took longer than it should have?
3. What did the user have to ask for that I should have produced unprompted?
4. What workspace knowledge would have saved me time?

Each learning routes by type:

| Kind of learning | Goes in |
|---|---|
| Generic RCA discipline | the skill: `SKILL.md` §6 / §8 / cascade-playbook |
| Workspace-specific quirk | `infra-knowledge/server-quirks.md` (or another `infra-knowledge/` file) |
| User preference / writing style | project memory as a `feedback` entry |
| New service relationship / slow query | `infra-knowledge/services.md` / `known-issues.md` |

This is the mechanism that makes the workspace *sharper* after each incident. Generic lessons travel with the skill; team-specific lessons travel with the infra-knowledge folder; user-specific preferences travel with the user. The audit trail in each incident's `learnings.md` cites which of these each rule went into, so the rule can be re-evaluated later when the evidence changes.

## Database safety

The workspace can open **read-only** production database shells — `mongosh`, `psql`, `mysql` — for the rare case where the cascade narrows to a specific table, collection, or query. Because these clients can in principle write, the workspace defends in three layers, but **only the first actually denies writes across every access path**:

1. **Role-level (the real guard).** The DB user in `.env` *must* be a true read-only role. This is your responsibility to provision; nothing in the workspace can enforce it, and a read-write role could still write through a non-libpq driver. This layer is mandatory.
2. **Session-level (CLI clients only).** `.envrc` sets `PGOPTIONS=-c default_transaction_read_only=on` (honoured by `psql` as the session default) and writes a MySQL option file with `init-command=SET SESSION TRANSACTION READ ONLY` — apply it via `mysql --defaults-extra-file="$XDG_CONFIG_HOME/mysql/my.cnf"`. Mongo's `?readPreference=secondary` is read *routing*, not a write block. A session can still opt back into read-write, so this is defence-in-depth, not a substitute for layer 1.
3. **Agent-level (per-clone convention).** The Claude Code allowlist should *not* pre-approve `psql`/`mysql`/`mongosh`, so every query prompts for approval. Keep the allowlist tight — a broad `Bash(python3 -c ...)` wildcard would let the agent reach a DB driver around the prompt.

Operationally: **`EXPLAIN` before any non-trivial SELECT, `LIMIT` every SELECT** (default `LIMIT 100`), and read each query before approving it. The full contract is in `CLAUDE.md` ("Database safety contract") and the root `README.md` ("Database access — read this once").
