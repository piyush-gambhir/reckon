# Mode: analyze

Questions about **shape over time** rather than faults: capacity headroom, cost drivers, performance profiling, traffic growth, "what should we optimize first".

> **Entered from [`../SKILL.md`](../SKILL.md).** That file carries the active-environment rule,
> the toolbelt, and how to read `infra-knowledge/`. Say which environment the numbers describe —
> capacity and cost conclusions from staging do not transfer to production, and quoting them
> as if they do is the characteristic failure of this mode.

**Use this mode when nothing is broken.** If something is currently wrong, that's [`investigate.md`](investigate.md); if you're checking *whether* anything is wrong, that's [`monitor.md`](monitor.md). Analysis runs over long windows and answers a planning question, not an operational one.

## 1. Frame the question as a decision

Analysis without a decision attached becomes a pile of charts. Before querying, pin down:

- **The decision it informs** — what someone will *do* differently depending on the answer (resize, optimize, re-architect, re-budget, defer).
- **The horizon** — 7 days · 30 days · a quarter. This sets both the window and the step size.
- **The unit that matters** — milliseconds, requests/sec, GB ingested, connections, currency. Say it explicitly; mixing units mid-analysis is how conclusions go wrong.
- **The threshold** — the number at which the answer flips. "We have headroom" is meaningless without knowing what running out looks like.

## 2. The four shapes

**Capacity / headroom.** Where is the ceiling and when do we hit it? Chart the saturation metric (connections vs `max_connections`, pool utilisation, CPU, memory, disk, IOPS, partition count) as a *percentage of its limit* over the horizon, fit the growth, and state a **time-to-exhaustion** with the assumption it rests on. Name the limit and where it's configured — an unattributed percentage isn't actionable.

**Performance profiling.** Where does time actually go across the fleet? Rank endpoints, spans, and queries by **total time contribution** (see §3), then decompose the top few into per-call cost vs call volume — the fix differs completely.

**Cost drivers.** What generates spend or data volume? Log/metric/trace ingestion by service, storage growth, expensive query patterns, cross-AZ transfer, over-provisioned capacity. Rank by contribution and pair each with the cheapest lever that moves it.

**Growth and traffic shape.** How is load changing, and is the shape changing with it? Peak-to-mean ratio, burstiness, and the mix of traffic across endpoints matter more than raw totals — a service whose peak/mean is widening will hit capacity limits long before its average suggests.

## 3. The ranking discipline — total contribution, not worst case

This is the one rule that most changes conclusions in this mode.

**Rank by `per-call cost × call volume`, not by the worst per-call number.** The slowest endpoint is very often not the one worth fixing. An endpoint at 2 s called 10×/min contributes 20 s/min; an endpoint at 40 ms called 5,000×/min contributes 200 s/min — ten times more, while looking perfectly healthy on every latency dashboard. Optimization effort should follow total contribution.

Then decompose the top contributors, because the remedy diverges:

- **High per-call, low volume** → make the operation cheaper (index, query shape, payload size, N+1).
- **Low per-call, high volume** → reduce calls (cache, batch, dedupe, fix a chatty caller) or accept it as the cost of the workload.
- **Both rising** → the workload changed; find what changed upstream before optimizing anything.

The same per-call-vs-volume decomposition drives the investigate cascade — there it isolates a *cause*, here it sets a *priority*. Same measurement, different question.

## 4. Method

- **Long windows, coarse steps.** Match step to horizon: ~15 m steps over 24 h, ~1 h over a week, ~1 d over a quarter. Fine steps over long windows return unusable volumes of data and hide the trend in noise.
- **Cover at least one full weekly cycle** for anything seasonal. Infra load is strongly weekly; a 3-day window will mislead about growth every time.
- **Aggregate first, drill second.** Establish the total, then decompose it. Starting from an interesting-looking individual query wastes the window.
- **Anchor to limits, not to zero.** Percent-of-limit is the actionable framing; absolute values are not, unless you also state the ceiling.
- **Read `infra-knowledge/<env>/known-issues.md` first.** A "discovery" the team documented long ago is not a finding, and known-slow queries change how you read a profile.

## 5. Pitfalls specific to analysis

1. **Retention silently truncates your window.** Ask for 90 days from a 30-day store and you get 30 days of data presented as if it were the answer — the trend line then describes the retention boundary, not the system. Enumerate datasources and their retention *before* choosing the horizon, and state the effective window in the output.
2. **Sampling distorts totals.** Tail-sampled traces are fine for finding *examples* and useless for computing *totals* — low-volume endpoints are dropped entirely and high-volume ones are scaled. Use metrics (counters/histograms) for anything summed or ranked; use traces to explain what the metrics point at.
3. **Never trend across a deploy or migration boundary without marking it.** A step change at a release is not growth. Overlay deploy annotations on any multi-week series and treat each side of a real step as a separate regime.
4. **Don't extrapolate linearly by default.** Most infra growth is stepwise (a customer onboards, a feature ships) or compounding, not linear. State the model you fitted and the assumption behind it; give a range, not a single date.
5. **Averages hide the thing you care about.** A stable mean can conceal a widening peak. Look at peak/mean and at percentiles before concluding "flat".
6. **An instant query answers "now", not "over the horizon".** Range queries are the default in this mode; an instant reading taken during a quiet period will contradict the history and look like a discrepancy when it's just the current moment.
7. **Cross-environment numbers don't transfer.** Staging capacity, cost, and performance profiles reflect staging's traffic. Never size production from them.

## 6. Output: the analysis writeup

Analyses are durable — they get revisited when the decision comes back around. Write to `incidents/` **only** if the analysis arose from an incident; otherwise keep it wherever the team keeps planning docs, and state the environment and window in the first line.

```markdown
# Analysis: <question> — <environment>

**Decision this informs:** <what changes based on the answer>
**Window:** <range> (effective: <what retention actually allowed>) · **Step:** <step>
**Headline:** <the answer, in one sentence, with its number>

## Method
<what was measured, from which source, and why that source>

## Findings — ranked by total contribution
| # | Item | Per-unit | Volume | Total | % of whole |
|---|------|----------|--------|-------|------------|

## Trend
<series + the model fitted; time-to-threshold with its assumption stated>

## Recommendations — ranked by leverage
1. <highest-leverage lever, the number it moves, and the estimated effort>

## Limits of this analysis
<retention truncation, sampling, unmonitored surfaces, regime changes excluded>
```

Keep the **"Limits of this analysis"** section honest and specific — an analysis that overstates its confidence gets used to justify spending, which is a more expensive mistake than a wrong RCA.
