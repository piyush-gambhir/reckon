---
title: Your first investigation
description: A walkthrough of how the agent investigates an alert end-to-end, from paste to written RCA.
---

The most reliable way to learn `rca-assist` is to run it on a real alert. This page walks through what the agent does — and what *you* should expect from it — using a recent investigation as a template.

## The hand-off

You paste the alert text directly into the agent:

```
[#36517] [FIRING:1] Conversational AI | P2-Warning: Error Rate >3% (10min)
```

The agent does **not** ask you to clarify. The skill picks up automatically because the title fits the trigger pattern (a service name, a severity, a symptom narrative).

## What happens next

The skill runs a six-level cascade. At each level it answers two questions: *where did the time/errors go* and *what fed this level with load or bad inputs*. It only descends when it can attribute quantitatively.

1. **Frame.** Convert the alert into a tight question. Service label = `CONVERSATIONAL_AI` (verified against `cubeapm metrics label-values service`, since label spellings are inconsistent — there's also `CONVERSATIONAL-AI-BACKEND` and `CONVERSATIONAL-AI-BACKEND-CRON`).
2. **Service-wide signal.** Plot 5xx rate over the alert window + 30 min buffer. Confirms the symptom is real and bounds the window.
3. **Endpoint attribution.** `topk` by `root_name` to find which endpoint owns the damage. In this incident, ~90% of 5xx came from `POST /conversation/internal/sms/v2/messages/outbound`.
4. **Per-call vs volume.** Did per-call latency change, or just call volume? Both jumped 3–5×. Constant `From`/account pair across error logs ruled out a request-driven shift.
5. **Sub-span / log evidence.** A single log query revealed the upstream cause: Twilio API rejecting outbound messages with *"Mismatch between the 'From' number +19786438481 and the account ACfea40b5ee787b06288092d836307a9e7"*. Stack trace points to `TwilioService.sendSms` and `SmsV2Service.sendOutbound`.
6. **Trigger search.** A 30-day novelty check confirmed this Mismatch was first observed today at 17:52 UTC — strong evidence something changed.

## What it writes

The agent saves a structured RCA folder at `incidents/<UTC-date>-<slug>/`:

```
incidents/2026-04-27-conversational-ai-twilio-mismatch/
├── RCA.md                           # the writeup
├── alert.txt                        # original notification text
├── learnings.md                     # what this incident taught us, with routing trail
└── evidence/
    ├── error-log-sample.json        # full log record incl. stack trace
    ├── error-message-counts.txt     # deduped error frequencies
    ├── mismatch-hits-1m.txt         # per-minute timeline
    └── mismatch-hits-30d.txt        # novelty proof
```

`RCA.md` is the single source of truth. `evidence/` is retention insurance — logs in CubeAPM rotate. `learnings.md` is the audit trail tying skill rules back to the incidents that motivated them.

## Where you stay in the loop

The agent does *not* take corrective action. It does not roll back a deploy, re-attach a Twilio number, or rotate a credential. It produces a written conclusion plus must-fix / should-fix / nice-to-have recommendations ranked by leverage. You read, you decide.

It also surfaces what it *couldn't* close. If a question can only be answered by something outside the three CLIs (a Jenkins job not yet wired in, a Twilio audit log, a `kubectl get pods` lookup), it goes into the RCA's "Unanswered questions" section with a specific "what would close it" line. Implicit gaps look like complete stories; explicit gaps unblock follow-ups.

## Post-incident self-review

After the RCA is acknowledged, the agent runs a four-question retrospective on its own investigation:

1. Did I correct any conclusion mid-flight? Why?
2. What took longer than it should have?
3. What did the user have to ask for that I should have produced unprompted?
4. What workspace knowledge would have saved me time?

Each learning routes to either the skill (generic discipline), `infra-knowledge/` (workspace fact), or project memory (your preferences). This is how the workspace gets sharper after every incident instead of repeating the same first-pass mistakes.
