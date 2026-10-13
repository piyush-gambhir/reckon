# Workflow: growth review

A weekly look at how search and on-site behaviour moved, ending in a short list of
evidence-backed actions. Uses `gsc`, `bing` and `clarity` connections; skip any the
org does not have and say so.

## Start

```bash
reckon task --question "Growth review, week of <date>" --area gtm
```

Read `knowledge/_shared/company.md` for the sites, key pages and target queries.
Save every read below with `--session <id>`.

## Reads

1. Totals and trend, current vs previous period:
   `reckon run gsc --session <id> -- performance --site <site> --last 28d --compare previous -o json`
   `reckon run gsc --session <id> -- trend --site <site> --last 3m --by week -o json`
2. What moved: `top queries` and `top pages` for `--last 7d` and `--last 28d`.
3. Opportunities: `insights striking-distance` and `insights low-ctr` (`--min-impressions 100`).
4. Bing: `reckon run bing --session <id> -- stats summary --days 28 --compare previous -o json`, then `crawl issues`.
5. Behaviour on the pages that moved: `reckon run clarity --session <id> -- export --days 3 --dimension URL -o json`
   and, for a page that lost conversions, `recordings list --days 2 --rage-clicks --count 50`.
   Clarity allows 10 export requests a day; spend them on the pages that matter.

## Reading the numbers

- Search Console's last two or three days are preliminary; compare full weeks.
- A traffic drop with steady impressions is a click-through problem (titles,
  snippets); a drop in impressions is ranking or indexing (`gsc inspect <url>`).
- Bing filters dates locally after fetching its top rows; treat it as direction, not totals.
- Seasonality and launches explain many moves: check `knowledge/` before calling something new.

## Output

Record each finding with `reckon note <id> --kind finding --evidence <ids>`, then
`reckon report <id>`. End with at most five actions, each with its page or query,
the evidence, and the expected effect. Writes (sitemaps, page edits, issues) are
for the operator.
