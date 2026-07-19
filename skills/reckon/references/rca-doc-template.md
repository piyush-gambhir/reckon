# RCA document template

Copy this skeleton to `incidents/<YYYY-MM-DD>-<slug>/RCA.md` (folder convention in [`incidents-convention.md`](incidents-convention.md)). Fill in every section; if a section has nothing to say, write one honest sentence explaining why rather than deleting it.

```markdown
# RCA: <one-line headline, e.g. "<Service> Apdex drop to 0.21">

|                        |                                                                    |
|------------------------|--------------------------------------------------------------------|
| **Date**               | YYYY-MM-DD                                                         |
| **Window**             | HH:MM–HH:MM LOCAL (HH:MM–HH:MM UTC)                                |
| **Peak impact**        | <worst number + timestamp>                                         |
| **Services affected**  | <primary service (metric)>, <downstream(s)>                        |
| **Environment**        | PROD / STAG / ...                                                  |
| **Duration visible**   | <minutes from onset to recovery>                                   |
| **Triggered by**       | <one line>                                                         |
| **Latent contributors**| <slow query X / fan-out pattern Y / missing rate limit Z>          |
| **Auto-recovery**      | Yes / No (+ reason)                                                |
| **Data gaps**          | <what we couldn't check and why>                                   |
| **Root-cause category**| <one leaf name from references/root-cause-taxonomy.md>             |
| **Confidence**         | <0.00–1.00> — <one line: what would raise it>                      |

---

## 0. Diagnosis (structured)

A machine-greppable summary so this incident joins the searchable corpus. Keep it terse; the prose
sections below justify it. Categories come from `references/root-cause-taxonomy.md`.

```yaml
root_cause_category: <leaf name>          # exactly one, narrowest the evidence supports
incident_status: <confirmed|open|mitigated|ongoing>
confidence: 0.00                          # 0.0–1.0; run the self-consistency check before setting
confidence_rationale: <one line — the single missing piece of evidence that would move this>
```

**Validated claims** — each backed by a specific number/query/log in §4 (cite the subsection):
- <claim> — §4.x
- <claim> — §4.x

**Open / unvalidated claims** — believed but not yet proven by tool evidence (hypotheses, not conclusions):
- <claim> — what would confirm it: <specific check> (also lives in §7)

> Before finalizing: run the **self-consistency check** in `references/root-cause-taxonomy.md` — if
> your prose's dominant subsystem disagrees with the category's group, resolve it or subtract ~0.15
> from `confidence` and note the tension here.

---

## 1. Incident summary

Two paragraphs. The first restates the symptom and timeline in plain prose. The second states the cause in one sentence and names the surface fix and the deeper fix.

---

## 2. Timeline

Bucketed metric values showing drop and recovery.

| Time (LOCAL) | <metric>       | Notes                         |
|--------------|----------------|-------------------------------|
| baseline     | <value>        | normal                        |
| T0           | <value>        | first anomalous bucket        |
| T-worst      | **<value>**    | worst                         |
| T-recovery   | <value>        | recovered                     |

---

## 3. Causal chain

ASCII arrow diagram. Example:

```
[Trigger]
   │
   ▼
<Service A> <endpoint>     ← traffic pattern / volume
   │
   ▼
<Service B> <endpoint>     ← per-call latency change
   │
   ▼
<DB query or queue>        ← latent bottleneck
```

One sentence under the diagram walking through it in words.

---

## 4. Evidence

A subsection per cascade level.

### 4.1 Symptom confirmation
<Apdex / p99 / error-rate metric and the query that produced it, with key numbers inlined.>

### 4.2 Endpoint attribution
<topk penalty/error table>

### 4.3 Per-call vs volume
<the two series side by side; call out which one dominated>

### 4.4 Sub-span breakdown
<table of ms/s contribution>

### 4.5 Downstream recursion
<same analysis applied one level down>

### 4.6 Root / trigger
<the query or event that proves the trigger>

### 4.7 No deploy / alert sanity
<if applicable: Grafana annotations checked, Jenkins builds checked>

---

## 5. Why recovery was automatic (or not)

<Describe the feedback loop that closed. If operator action was required, list who did what.>

---

## 6. Root causes (ranked by leverage)

### RC-1. <headline>
**Severity:** High / Medium / Low.
<Two to five lines explaining. For latent bugs, cite baseline data proving "this was always broken".>

### RC-2. <headline>
…

### RC-3 (contributing). <headline>
…

---

## 7. Unanswered questions & data gaps

### Q1. <question>
**Status:** unresolved.
**What would close it:** <specific logs / DB query / commit range to check>

### Q2. <question>
…

---

## 8. Recommendations

Give each recommendation a **Status** so a later incident can tell what was actually done: `proposed` (default) · `ticketed <link>` · `shipped <date>` · `declined <reason>`. Before investigating a recurring symptom, grep prior RCAs for the affected service's open (`proposed`/`ticketed`) recommendations — an unshipped must-fix from a past incident is often the root cause of the next one.

### Must-fix
1. <RC-1 remediation> — *Status: proposed*
2. <RC-2 remediation> — *Status: proposed*

### Should-fix
3. <RC-3 remediation> — *Status: proposed*
4. <architectural follow-up> — *Status: proposed*

### Nice-to-have (observability / process)
5. <alerting improvement> — *Status: proposed*
6. <runbook / audit trail> — *Status: proposed*

---

## 9. Appendix: queries used

The exact queries (PromQL, LogsQL, CLI invocations) that produced every number in §4. Copy-pasteable.

---

## 10. Reproducibility

Short block showing how to re-run this investigation from scratch, including any login prerequisites.
```
