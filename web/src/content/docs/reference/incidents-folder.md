---
title: The incidents/ folder
description: How the skill organises each investigation on disk — RCA, alert text, evidence, and per-incident learnings.
---

Every investigation produces a folder at `incidents/<YYYY-MM-DD>-<slug>/`. This is the durable, on-disk record: the alert that fired, the RCA writeup, the supporting evidence, and a short retrospective on what *this specific incident* taught the team.

## Naming

```
incidents/<YYYY-MM-DD>-<short-slug>/
```

- **Date** is the **UTC** date the incident *started* — not the date the alert fired (they can differ across midnight UTC).
- **Slug** is hyphenated, usually `<service>-<symptom>` or `<service>-<root-cause>`. Examples: `2026-04-27-conversational-ai-twilio-mismatch`, `2026-04-19-media-service-apdex`.

## Layout

```
incidents/<YYYY-MM-DD>-<slug>/
├── RCA.md                          # required. The writeup.
├── alert.txt                       # required when an alert kicked off the investigation.
├── learnings.md                    # required. Per-incident retrospective with routing trail.
└── evidence/                       # raw outputs that back specific claims in RCA.md.
    ├── error-log-sample.json
    ├── error-message-counts.txt
    ├── <signal>-hits-1m.txt
    ├── <signal>-hits-30d.txt
    └── ...
```

## Why per-folder

- **Provenance.** Every claim in an RCA cites a number. Saving the raw output next to the RCA means a reader can verify without re-running queries against logs that may have rotated.
- **Retention insurance.** CubeAPM logs and traces rotate; the snapshot saved at write time will not.
- **Auditability.** `learnings.md` is the "where did this rule come from" trail. When someone reads `SKILL.md` §6 #6 ("don't trust fresh log buckets") six months from now, they can find the originating incident — and re-evaluate the rule against new evidence if the team has since fixed the underlying ingestion lag.
- **Discipline.** Forcing a slug + a learnings file at write time keeps the team honest about retrospectives. A folder with only `RCA.md` and no `learnings.md` reads as "we didn't actually reflect."

## What gets committed

The incident folders are **gitignored by default**. They reference live service names, IPs, trace IDs, and timelines that should not be published. The convention itself — `incidents/README.md` and any `incidents/*.example/` reference folders — is tracked, so the layout travels with the skill but tenant data does not.

This mirrors the [`infra-knowledge/`](/guides/customizing-infra-knowledge/) split: the templates ship with the product; the filled-in copies stay local.

If you want to share a specific incident publicly (postmortem, blog post, conference talk), redact and commit it manually as a one-off — or copy it into an `incidents/<slug>.example/` folder, which is not gitignored.

## RCA.md structure

The skill's writeup template (`skills/rca-assist/references/rca-doc-template.md`) defines ten sections. Required, in order:

1. Header table (date, window in both local and UTC, peak impact, services affected, trigger, latent contributors, auto-recovery y/n, data gaps).
2. Incident summary (1–2 paragraphs an engineer could paste into a postmortem).
3. Timeline (bucketed metric values; 2-min buckets for ~30 min windows).
4. Causal chain diagram (ASCII arrows: symptom → service → endpoint → downstream → root).
5. Evidence (one subsection per cascade level, with the queries inlined).
6. Why recovery was automatic (or not).
7. Root causes, ranked by leverage.
8. Unanswered questions & data gaps.
9. Recommendations: must-fix / should-fix / nice-to-have.
10. Appendix: queries used (so the next on-call can re-run them).

Optimised for two readers: the team that owns the service reads it once and knows what to do, *and* a future on-call searching for "apdex drop" finds it and learns the pattern.
