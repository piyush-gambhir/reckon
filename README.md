# reckon

One workspace for running several businesses with a coding agent.

Each business is an **org**. An org has its own environments (prod, uat, dev),
tool connections, company knowledge and saved work. You open one org in one
environment, and Claude Code or Codex works on that business through the tools
you already use: development, product, growth and operations.

📖 **Docs site:** [`web/`](web/) (Next.js + Fumadocs, static export to Cloudflare Workers Assets).
Plan and status: [docs/PRODUCT-PLAN.md](docs/PRODUCT-PLAN.md); long view: [docs/ROADMAP.md](docs/ROADMAP.md).

## How it works

```bash
reckon org add acme --envs prod,uat,dev
reckon connect jenkins-prod --org acme --provider jenkins --for-env prod     # one server per environment
reckon connect jenkins-uat  --org acme --provider jenkins --for-env uat
reckon connect cubeapm      --org acme --provider cubeapm --label prod=PROD --label uat=UAT   # one server, all environments
reckon connect gsc          --org acme --provider gsc                         # no environments at all
reckon use acme/prod
reckon verify            # one live read per connection
reckon open              # starts Claude Code (or Codex) in acme's folder, pinned to prod
```

`reckon connect` prints a credentials file to fill in (or run `reckon login <connection>`
for CLIs with a browser login). Inside the agent, every tool goes through Reckon:

```bash
reckon run jenkins -- build list deploy-api -o json      # the prod Jenkins, read-only
reckon run gsc -- top queries --site sc-domain:acme.com --last 28d -o json
```

- **The agent never holds credentials.** `reckon run` loads one connection's settings
  into one command. `reckon open` strips tool credentials from the agent's environment.
- **Reads are read-only.** Reckon forces each CLI's read-only and no-prompt modes and
  refuses arguments that would change the target or switch safety off.
- **Writes are yours to apply.** The agent prepares one (`reckon act prepare
  github.issue.create ...`); you read the preview and run `reckon act apply <id>`
  in your own terminal. Production and org-wide accounts need the org name typed.
- **The context cannot drift.** An agent stays on the org and environment it started
  with. A connection from another environment is refused, and so is another
  environment's label on a shared server like CubeAPM.
- **Each org has its own folder** (`~/.reckon/orgs/<org>/workspace`), so Claude Code
  and Codex keep each business's memory and conversations apart, and
  `claude --continue` resumes the right one.

These rules stop mistakes, not a hostile process running as your user. Database
roles must still be read-only on the server.

## Saved work

```bash
reckon task --question "Weekly search review" --area gtm
reckon run gsc --session <id> -- insights striking-distance --site sc-domain:acme.com --last 28d -o json
reckon note <id> --kind finding --text "..." --evidence <evidence-id>
reckon report <id>
reckon history
```

Sessions keep every read with its command, connection, time and hashes. Incident
investigations (`reckon debug`) add mapped services, typed collection, hypotheses
and promotion to an RCA; see [docs/INVESTIGATIONS.md](docs/INVESTIGATIONS.md).
Try one offline: `reckon demo`.

## Tools

| Area | Tools (connection provider) |
| --- | --- |
| Development | `jenkins`, `github` (gh), `kubernetes` (kubectl), `aws` |
| Operations | `cubeapm`, `grafana`, `elasticsearch` (es), `nginxpm`, plus `postgres`, `mysql`, `mongodb`, `clickhouse`, `redis`, `kafka`, `kafka-rpk` (each run asks you first) |
| Product | `jira`, `clarity` |
| Growth | `gsc` (Google Search Console), `bing` (bwt) |

`reckon connect --list` shows them all. The nine CLIs above that are ours
(grafana, jenkins, cubeapm, es, jira, nginxpm, gsc, bwt, clarity) are pinned in
[scripts/cli-releases.csv](scripts/cli-releases.csv) and installed by setup.

## Setup

Requires Python 3.9+ (no packages). macOS and Linux:

```bash
git clone https://github.com/piyush-gambhir/reckon.git
cd reckon
bash scripts/setup.sh        # installs missing tools, links ~/.reckon/bin/reckon
export PATH="$HOME/.reckon/bin:$PATH"
```

`setup.sh` uses Homebrew on macOS and apt or dnf on Linux, verifies checksums for
downloaded binaries, and is idempotent. Windows: use WSL2 and the same script;
native PowerShell (`scripts\setup.ps1`, `scripts\reckon.ps1`) is experimental.

**Coming from an older checkout** (`.env.production`, `infra-knowledge/`,
`incidents/` in this folder)? Copy it into an org, then delete the old files once
you have checked it:

```bash
reckon migrate --org acme --rename production=prod --rename staging=dev --share cubeapm --dry-run
reckon migrate --org acme --rename production=prod --rename staging=dev --share cubeapm
```

## Agent guide

Inside an org, the agent follows [WORKSPACE.md](WORKSPACE.md) (imported by the
org's `CLAUDE.md`) and the `reckon` skill for incident method. Developing Reckon
itself: [CLAUDE.md](CLAUDE.md).
