---
title: Post-incident self-review
description: The four-question retrospective the agent runs after every RCA, and how each learning gets routed to the right home.
---

After every RCA is acknowledged, the agent runs a short retrospective on its own investigation. This is mandatory — silently skipping it is treated as failure, not as the default.

## The four questions

1. **Did I correct any conclusion mid-investigation?** (Recovery time, root cause, blast radius.) If yes, *why* did I get it wrong the first time? Bad data, missed cross-check, or discipline lapse (speculation, premature closure)? Each correction is a candidate learning.
2. **What took longer than it should have?** Wasted round-trips on the wrong tool, dead-end queries, hunting for things `infra-knowledge/` already documents.
3. **What did the user have to ask for that I should have produced unprompted?** Caller identification, alert state confirmation, deploy correlation, etc.
4. **What workspace knowledge would have saved me time?** Anything not in `infra-knowledge/` yet but should be.

## The routing table

Each learning has exactly one right home:

| Kind of learning | Goes in |
|---|---|
| Generic RCA discipline (any service, any stack) | the skill: `SKILL.md` §6 / §8 / cascade-playbook |
| Workspace-specific quirk (CLI behaviour, server bug, label gotcha) | `infra-knowledge/server-quirks.md` (or another `infra-knowledge/` file) |
| User preference / writing style / approval pattern | project memory as a `feedback` entry |
| New service relationship, slow query, fan-out fact | `infra-knowledge/services.md` / `known-issues.md` |

The split matters: skill learnings travel with the *product*, infra-knowledge learnings travel with the *workspace clone*, memory learnings travel with the *user*. Putting a workspace-specific quirk into the skill pollutes the product for other teams; putting a generic discipline rule into infra-knowledge means it never reaches anyone else.

## The two-line minimum

Each learning entry needs at minimum:

1. **The rule itself** (what to do or not do).
2. **A `Why:` line** citing the actual numbers / timestamps / phrases from the originating incident as evidence.

The why-line is the part that ages well. When evidence changes — e.g., the team fixes the underlying logs ingestion lag, or rewrites the service that had the unmatched Twilio config — a future reader can decide whether the rule still applies. A bare rule with no evidence pointer becomes folklore.

## "Nothing was learned" is a real outcome

If the investigation ran cleanly — no mid-flight corrections, no dead ends, no missed steps — the agent says so explicitly in its wrap-up. Silent skipping looks the same as forgetting. Two clean RCAs in a row is a real signal about the workspace's maturity; it should be visible.

## How learnings actually accumulate

A look at the workspace's pitfalls list (`SKILL.md` §6) shows how this plays out over time:

- Pitfalls #1–#5 came from the initial design — patterns the methodology had to address from day one.
- Pitfall #6 (don't trust fresh log buckets) came from a real incident where the agent declared recovery 20 min early because logs ingestion was lagging.
- Pitfall #7 (caller discovery for `/internal/` endpoints) came from the same incident — the agent didn't think to ask "who's calling this?" until the user prompted, by which point the caller's traces had been tail-sampled.
- Pitfall #8 (recognise the alert source before chasing the rule) came from the same incident's wasted Grafana-alert-list round-trips.

Each pitfall has a per-incident `learnings.md` that points back to the originating folder. Six months later, anyone can ask "why does the skill say this?" and get a precise, evidence-backed answer.
