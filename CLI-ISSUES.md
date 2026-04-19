# CLI issues — open items

Running log of bugs, gaps, and papercuts found in the `grafana`, `jenkins`, and `cubeapm` CLIs while using this workspace for real RCA work. Resolved items get removed as they land — the list is a to-do, not a history. See git log of each CLI repo for the fix context.

Legend: 🚧 in progress · ❌ open

_No open items at time of last cleanup (2026-04-20)._

---

## How new items should be filed

Add a new section under the relevant CLI and use this shape:

```
### <legend> <n>. <short headline>
**Symptom:** exact command and the error / unexpected behaviour.
**Cause:** what's actually wrong (server quirk, CLI bug, doc drift).
**Impact:** who/what it blocks.
**Fix direction:** the likely path — flag, fallback, new endpoint.
```

Keep entries short. If the fix turns out to be long, link to a design doc rather than inline it.

Remove the entry when the fix lands, rather than flipping it to ✅ — the git log keeps the history.
