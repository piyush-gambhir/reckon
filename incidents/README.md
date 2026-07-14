# incidents/

One folder per real incident. Each folder is the durable, on-disk record of an investigation: the alert that fired, the RCA document, the supporting evidence, and a short retrospective on what *this specific incident* taught the team.

This folder mirrors the `infra-knowledge/` split: the **convention** is shared (this README, plus any `*.example/` reference folders) but the actual incident folders are **local-only**. They contain real service names, IPs, account IDs, trace IDs, and timelines that should not be published.

## What's tracked vs ignored

- ✅ `incidents/README.md` — this file. The convention.
- ✅ `incidents/*.example/` (if any are added later) — reference / training incidents with redacted data.
- 🚫 `incidents/<YYYY-MM-DD>-<slug>/` — every real incident folder is gitignored. Commit one manually only after redacting it.

The exact gitignore pattern lives at the repo root.

## Folder naming

```
incidents/<YYYY-MM-DD>-<short-slug>/
```

- Date is the **UTC** date the incident *started*, not the date the alert fired (they can differ when an incident crosses midnight UTC).
- Slug is a hyphenated phrase a teammate would type into search — usually `<service>-<symptom>` or `<service>-<root-cause>`.
- Examples: `2026-04-27-payments-gateway-mismatch`, `2026-04-19-search-api-apdex`.

## Folder layout

The skill writes the following structure. Anything here is optional except `RCA.md`; add files only when they carry information that's not already in the RCA.

```
incidents/<YYYY-MM-DD>-<slug>/
├── RCA.md                    # the writeup. Required. Follows skills/rca-assist/references/rca-doc-template.md.
├── alert.txt                 # the original notification text that triggered the investigation.
├── learnings.md              # short retrospective: what I got wrong, where each lesson now lives.
├── evidence/                 # raw artifacts that back specific claims in the RCA.
│   ├── error-log-sample.json # one full error record (so the stack trace is preserved if logs are retained shorter than the RCA).
│   ├── error-message-counts.txt # deduped error message frequencies for the window.
│   ├── <signal>-hits-1m.txt  # per-minute log-hit timeline showing onset + recovery.
│   ├── <signal>-hits-30d.txt # novelty check showing this error was new on the incident date.
│   └── ...                   # any other long output that would bloat the RCA inline.
└── notes/                    # optional. Free-form scratch notes from the investigation; rarely needed.
```

`RCA.md` is the single source of truth. `evidence/` exists so a future reader can verify a specific claim without re-running queries against logs that may have rotated. `learnings.md` is the per-incident audit trail — it cites *where* each learning was routed (skill / `infra-knowledge/` / project memory) so a future on-call can trace a rule back to the incident that motivated it.

## Why per-incident folders

- **Provenance.** Every claim in an RCA cites a number that came from a specific query at a specific time. Saving the raw output next to the RCA means a reader doesn't have to trust *or* re-derive — they can read both.
- **Retention insurance.** Logs and traces in CubeAPM rotate. The RCA may live forever; the evidence behind it doesn't, unless it's snapshotted at write time.
- **Auditability.** `learnings.md` is the "where did this rule come from" trail. Six months from now, when someone reads SKILL.md §6 #6 ("don't trust fresh log buckets"), they can find the originating incident and re-evaluate whether the rule still applies in a workspace whose logs may have been re-instrumented since.
- **Discipline.** Forcing a slug + a learnings file at write time keeps the team honest about retrospectives. A folder with only `RCA.md` and no `learnings.md` reads as "we didn't actually reflect."

## How to add an incident manually

If you start an investigation outside the skill (a quick triage that turns into an RCA, for example):

```bash
SLUG=2026-mm-dd-<service>-<symptom>
mkdir -p "incidents/$SLUG/evidence"
# Then write RCA.md, alert.txt, learnings.md, and evidence/*
```

The skill (when invoked via `/rca-assist` or by the `rca-assist` agent) will use this same layout automatically.

## Export to PDF

Export an RCA Markdown document, optionally choosing the output path:

```bash
bash scripts/export-rca-pdf.sh PATH_TO_RCA_MD OPTIONAL_OUTPUT_PDF
```

Or pass an incident folder to export its `RCA.md` to `RCA.pdf` beside it:

```bash
bash scripts/export-rca-pdf.sh incidents/SOME_DIR
```

The exporter prefers `pandoc` with `weasyprint`, `wkhtmltopdf`, `tectonic`, or
`xelatex` (in that order). If that combination is unavailable, it falls back to
`npx --yes md-to-pdf` when Node.js is installed; otherwise it prints install
hints. Existing PDFs require `--force` to be replaced.
