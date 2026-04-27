---
title: Installation
description: Clone the workspace, install the three CLIs, and seed credentials so the agent is ready when an alert fires.
---

`rca-assist` is a *workspace*, not a package. You clone it, fill in real credentials, and `cd` in whenever you need to investigate. The agent's tooling lives globally (skills installed once, CLIs on `$PATH`), so each clone stays small.

## Prerequisites

- **`direnv`** — for repo-local credential isolation. `brew install direnv` and hook it into your shell (`eval "$(direnv hook zsh)"` or equivalent).
- **`jq`** — used by the skill's example commands. `brew install jq`.
- **Claude Code** — the agent runtime. See the [Claude Code docs](https://docs.anthropic.com/en/docs/claude-code) for install instructions.
- **Three CLIs** — install via `npx skills add` once globally:

  ```bash
  npx skills add grafana
  npx skills add jenkins
  npx skills add cubeapm
  npx skills add rca-assist
  ```

## Clone the workspace

```bash
git clone https://github.com/piyush-gambhir/rca-assist.git
cd rca-assist
```

## Seed credentials

The workspace ships an `.env.example` you copy and fill in. **This clone is intended for production credentials only** — if you need staging or UAT, use a different clone so the agent never crosses environments during an RCA.

```bash
cp -n .env.example .env
$EDITOR .env
direnv allow
```

`.envrc` sets `XDG_CONFIG_HOME` to `.config/` inside the workspace, so any saved CLI profiles are isolated from your global `~/.config/` ones. Either approach works:

- **Preferred — env vars in `.env`.** Direnv exports them automatically when you `cd` in. No interactive logins, immediately usable.
- **Fallback — saved profiles.** Run `grafana login`, `jenkins login`, `cubeapm login` from inside the workspace and the profiles land at `.config/<cli>/config.yaml`.

## Seed team-specific knowledge

The `infra-knowledge/` folder holds your team's facts (service inventory, server quirks, known-slow queries). The repo ships *templates* (`*.example.md`); you copy each to its real name and fill in.

```bash
cd infra-knowledge
for f in *.example.md; do cp -n "$f" "${f%.example.md}.md"; done
$EDITOR services.md   # populate as you go
cd ..
```

The filled-in files are gitignored — they hold tenant-specific data that should not be committed. The skill consults them automatically before each investigation.

## Verify

```bash
grafana user current -o json
jenkins status -o json
cubeapm metrics label-values service -o json | jq -r '.[].VALUE' | head
```

If all three return real data, you're set. The next time an alert fires, paste it into the agent and let it run the cascade. See [First investigation](/guides/first-investigation/) for what to expect.
