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

Pitfalls #6–#8 came directly from real on-call incidents and got back-ported. The pattern: any time the agent corrects a conclusion mid-investigation, the rule that would have prevented the mistake gets added here.

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
