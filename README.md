# rca-assist

Root Cause Analysis assistant powered by Grafana, Jenkins, and CubeAPM — plus a read-only ops toolbelt (aws, gh, kcat, rpk, mongosh, psql, mysql).

📖 **Docs site:** see [`web/`](web/) (Astro + Starlight, deployed to Cloudflare Pages). Run `cd web && npm install && npm run dev` for local preview.

## Overview

`rca-assist` is an agent workspace for investigating production incidents and performing root cause analysis. It wires together twelve read-only observability, CI/CD, and infrastructure CLIs (plus an optional thirteenth, `es`, for ES/ELK log stores — see the table below) under a single, isolated credential environment so a coding agent can correlate signals across systems in one session. It runs under **Claude Code** (subscription or API key), **Codex CLI** (ChatGPT subscription or API key), or any agent runtime that reads `AGENTS.md` — see [Agent runtimes](#agent-runtimes).

This clone is intended to be **production-only**. Put only production Grafana, Jenkins, and CubeAPM credentials in this workspace. If you ever need staging or UAT, use a separate clone so the agent never mixes environments during an RCA.

**Tools available to the agent:**

| CLI | Covers |
|-----|--------|
| [`grafana`](https://github.com/piyush-gambhir/grafana-cli) | Dashboards, datasources, alerts, annotations |
| [`jenkins`](https://github.com/piyush-gambhir/jenkins-cli) | Jobs, builds, pipelines, logs, nodes |
| [`cubeapm`](https://github.com/piyush-gambhir/cubeapm-cli) | Distributed traces, PromQL metrics, LogsQL logs |
| [`aws`](https://aws.amazon.com/cli/) | CloudWatch metrics/logs, ALB/ELB, ECS, SQS, RDS, S3 |
| [`gh`](https://cli.github.com/) | PRs, GitHub Actions runs, releases, issues |
| [`kcat`](https://github.com/edenhill/kcat) | Kafka metadata + tail messages from a topic (read-only by usage) |
| [`rpk`](https://docs.redpanda.com/current/reference/rpk/) | Kafka consumer-group lag + cluster info (talks to vanilla Kafka/MSK/Confluent too) |
| [`kubectl`](https://kubernetes.io/docs/reference/kubectl/) | Pod/deploy state, events, rollout history (read-only by usage) |
| [`redis-cli`](https://redis.io/docs/latest/develop/tools/cli/) | Redis diagnostics — INFO, SLOWLOG, LATENCY (read-only by usage) |
| [`mongosh`](https://www.mongodb.com/docs/mongodb-shell/) | MongoDB shell — read-only DB role required |
| [`psql`](https://www.postgresql.org/docs/current/app-psql.html) | PostgreSQL shell — read-only DB role required |
| [`mysql`](https://dev.mysql.com/doc/refman/8.0/en/mysql.html) | MySQL shell — read-only DB role required |
| [`es`](https://github.com/piyush-gambhir/es-cli) *(optional)* | Elasticsearch/ELK log stores — cluster health, index state, Query DSL/SQL search. Client-side read-only enforced via `ES_READ_ONLY=true` from `.envrc`. |

## Platform support

| Platform | How tools are installed | direnv | Notes |
|---|---|---|---|
| macOS (Apple Silicon or Intel) | Homebrew + `go install` | ✓ native | First-class; all tools available (12 CLIs + direnv + jq). |
| Linux Debian/Ubuntu (`apt`) | signed packages + verified rpk/kubectl downloads + pinned `go install` | ✓ native | Some tools require their vendor's signed package repository. |
| Linux Fedora/RHEL family (`dnf`) | signed packages + verified rpk/kubectl downloads + pinned `go install` | ✓ native | Some tools require their vendor's signed package repository. |
| Other Linux (Arch, openSUSE, Alpine, …) | manual install | ✓ native | Use the manual command list per tool. |
| Windows + WSL2 *(recommended for Windows users)* | inherits Linux path inside WSL | ✓ native | First-class — run `bash scripts/setup.sh` inside WSL. |
| Windows native (PowerShell) | winget + `go install` | ✗ — use [`scripts/activate.ps1`](scripts/activate.ps1) | Partial: `direnv`, `kcat`, `rpk` have no native Windows port. |

## Setup

The setup scripts are **idempotent** — every install function checks whether the binary is already on `PATH` and skips it if so. Re-run any time you add a new tool to the workspace; only the missing ones get installed.

### macOS

```bash
git clone https://github.com/piyush-gambhir/rca-assist.git
cd rca-assist
bash scripts/setup.sh
```

Requires [Homebrew](https://brew.sh/). `scripts/setup.sh` installs missing tools via brew, installs the custom Go-based CLIs via `go install`, seeds `.env` from `.env.example`, seeds `infra-knowledge/*.md` from the `.example.md` templates, and runs `direnv allow`.

### Linux (Debian/Ubuntu or Fedora/RHEL family)

```bash
git clone https://github.com/piyush-gambhir/rca-assist.git
cd rca-assist
bash scripts/setup.sh
```

The same `scripts/setup.sh` auto-detects the distro via `/etc/os-release` and dispatches to `apt` or `dnf`. It verifies checksums for pinned `rpk` and `kubectl` downloads and uses signed package repositories for AWS CLI and mongosh; when a trusted repository is unavailable it fails closed with manual verification guidance. Other distros print a "manual install required" message.

> **Hooking direnv into your shell** (one-time): add `eval "$(direnv hook bash)"` to `~/.bashrc` (or the zsh equivalent to `~/.zshrc`). Without this, `.envrc` won't auto-load when you `cd` into the repo.

### Windows

**Recommended: use WSL2.** Native Windows is missing reliable ports of `direnv`, `kcat`, and `rpk` — and those are central to the workspace. With WSL2 you get the full Linux experience with no compromises.

```powershell
# One-time WSL setup (PowerShell as Administrator):
wsl --install
# Reboot, then inside WSL:
git clone https://github.com/piyush-gambhir/rca-assist.git
cd rca-assist
bash scripts/setup.sh
```

**Native PowerShell (partial support):** if WSL isn't an option, `scripts/setup.ps1` installs 9 of the 13 tools natively — 6 via winget (`aws`, `gh`, `mongosh`, `psql`, `mysql`, `kubectl`) and 3 via `go install` (`grafana`, `jenkins`, `cubeapm`); `direnv`, `kcat`, `rpk`, and `redis-cli` have no clean Windows port and need WSL2. Note that the winget `psql`/`mysql` packages install the full server bundles and may not place the client on `PATH` — see [`scripts/setup.ps1`](scripts/setup.ps1) comments. Then `scripts/activate.ps1` is the direnv replacement — dot-source it once per PowerShell session to load `.env` and apply the safety env vars.

```powershell
git clone https://github.com/piyush-gambhir/rca-assist.git
cd rca-assist
.\scripts\setup.ps1
notepad .env                      # fill in real credentials
. .\scripts\activate.ps1          # NOTE the leading dot+space
```

To auto-activate `.env` in every PowerShell session inside this folder, add to your `$PROFILE`:

```powershell
if ($PWD.Path -like '*\rca-assist*') { . .\scripts\activate.ps1 }
```

### Manual install (any platform)

If the script doesn't fit (unsupported distro, locked-down corporate machine, etc.), install each tool yourself using whichever package manager you have. Tool-by-tool install commands are documented in [`scripts/setup.sh`](scripts/setup.sh) (one `install_<tool>` function per tool — read those for the canonical install path on each platform).

After tool installation, finish workspace setup manually:

```bash
cp -n .env.example .env                                # then edit with real credentials
cd infra-knowledge
for f in *.example.md; do cp -n "$f" "${f/.example/}"; done
cd ..
direnv allow
```

Verify every connection (one safe read per tool):

```bash
grafana user current -o json
jenkins status -o json
cubeapm metrics label-values service -o json   # canonical service inventory (not `traces services`)
aws sts get-caller-identity --output json
gh auth status
kcat -L -b "$KAFKA_BOOTSTRAP_SERVERS" -X security.protocol=$KAFKA_SECURITY_PROTOCOL \
     -X sasl.mechanism=$KAFKA_SASL_MECHANISM -X sasl.username=$KAFKA_SASL_USERNAME \
     -X sasl.password=$KAFKA_SASL_PASSWORD | head -20
rpk cluster info                               # RPK_BROKERS / RPK_SASL_* derived from KAFKA_* by .envrc
kubectl get ns                                 # needs KUBECONFIG seeded: aws eks update-kubeconfig --name <cluster>
redis-cli -u "$REDIS_URL" PING
mongosh "$MONGODB_URI" --eval 'db.runCommand({ping:1})'
psql -c "SELECT current_user, current_database(), pg_is_in_recovery();"
mysql --defaults-extra-file="$XDG_CONFIG_HOME/mysql/my.cnf" -e "SELECT CURRENT_USER(), DATABASE(), @@transaction_read_only;"
es cluster health -o json                      # optional — only if ES_URL is set
```

> **DB safety check:** The Postgres command should report `pg_is_in_recovery = t` (you're on a replica) and `SHOW default_transaction_read_only` should be `on`; the MySQL command should report `@@transaction_read_only = 1`. If Postgres isn't read-only, `.envrc` didn't reload — run `direnv allow`. If **MySQL** isn't read-only, you almost certainly ran a bare `mysql` instead of `mysql --defaults-extra-file="$XDG_CONFIG_HOME/mysql/my.cnf"` — the mysql client has no read-only env var, so the option file is the only thing that applies it.

## How credentials are isolated

`.envrc` sets `XDG_CONFIG_HOME` to `.config/` inside this directory and also loads `.env` / `.env.local` when present. That gives you two repo-local authentication modes:

- **Preferred:** store credentials in `.env` or `.env.local` and let direnv export them automatically whenever you enter this repo.
- **Fallback:** run `grafana login`, `jenkins login`, `cubeapm login`, `aws configure`, or `gh auth login` inside this directory to save per-repo CLI profiles under `.config/`.

`grafana`, `jenkins`, `cubeapm`, and `gh` honour `XDG_CONFIG_HOME` natively, so their saved state lands inside `.config/` automatically. `aws` does not — `.envrc` therefore also exports `AWS_CONFIG_FILE` and `AWS_SHARED_CREDENTIALS_FILE` to `.config/aws/`, so `aws configure` writes here instead of `~/.aws/`.

Either way, credentials stay isolated from your global `~/.config/` and `~/.aws/` profiles. You can have different credentials per clone of this repo.

Config files land at:
- `.config/grafana-cli/config.yaml`
- `.config/jenkins-cli/config.yaml`
- `.config/cubeapm-cli/config.yaml`
- `.config/aws/{config,credentials}`
- `.config/gh/{config.yml,hosts.yml}`

Kafka and database tools (`kcat`, `rpk`, `mongosh`, `psql`, `mysql`) don't have a saved-profile mode here — they read credentials directly from the env vars you set in `.env`.

### Kafka access — read this once

`kcat` and `rpk` have **no read-only mode**, so Kafka safety is two layers, like the databases:

1. **Broker-level (the real guard)** — the SASL principal in `.env` should carry only `Describe`/`Read` ACLs (MSK IAM: `kafka-cluster:Connect`, `Describe*`, `ReadData`; no `WriteData`/`Create*`/`Delete*`/`Alter*`). Provision it like the read-only DB role.
2. **Usage-level** — only read commands are documented and should be allowlisted (`kcat -L/-C/-Q`, `rpk cluster info`, `topic list/describe/consume`, `group list/describe`). One trap that looks like a read but isn't: consuming **with a group id** (`kcat -G`, `rpk topic consume -g`) joins the production consumer group, triggering a rebalance and committing offsets. Always consume group-less.

### Database access — read this once

The DB clients (`mongosh`, `psql`, `mysql`) can in principle modify production data. The workspace defends against this in three layers, but **only layer 1 actually denies writes across every access path** — the other two harden the CLI clients and are defence-in-depth, not substitutes:

1. **Role-level (the real guard)** — every DB user named in `.env` MUST be a true read-only role at the database. This is your responsibility to provision; the CLI cannot enforce it, and a read-write role can still write through a non-libpq driver (e.g. a `python3` script using psycopg2/pymysql), so this layer is mandatory.
2. **Session-level (CLI clients only)** — `.envrc` sets `PGOPTIONS=-c default_transaction_read_only=on`, which `psql` honours as the session *default* (a session can still opt back in with `BEGIN READ WRITE`). For MySQL there is no read-only env var, so `.envrc` writes `$XDG_CONFIG_HOME/mysql/my.cnf` with `init-command=SET SESSION TRANSACTION READ ONLY` — apply it by invoking `mysql --defaults-extra-file="$XDG_CONFIG_HOME/mysql/my.cnf"`. MongoDB's `MONGODB_URI` carries `?readPreference=secondary`, which is read *routing*, not a write block.
3. **Agent-level (per-clone convention)** — the Claude Code allowlist in `.claude/settings.local.json` should deliberately *not* pre-approve `psql`, `mysql`, or `mongosh`, so every query prompts you for permission. Friction = safety. Because `.claude/` is gitignored, this is a convention each clone must uphold — keep the allowlist tight and never add a broad `Bash(python3 -c ...)` wildcard, which would let an agent reach a DB driver around the prompt.

## Usage with Claude Code

Open this directory in Claude Code (`claude`) and describe the incident. The agent will follow the RCA workflow in `CLAUDE.md` to correlate alerts, traces, metrics, logs, and deployment history across all three tools.

Example prompts:
- *"The checkout service latency spiked at 14:30 UTC. What happened?"*
- *"Which Jenkins build broke the payments pipeline and when did it start?"*
- *"Find all error traces from the auth service in the last hour."*

## Incidents viewer (UI)

A zero-dependency local web UI for browsing your RCA corpus — incident list with filtering, rendered RCA/learnings markdown, raw alert text, and every evidence file, including the legacy root-level `RCA-*.md` writeups:

```bash
node ui/serve.mjs        # → http://localhost:7777
```

It binds `127.0.0.1` only and serves the gitignored `incidents/` folder read-only — nothing leaves the machine. (The Astro site under [`web/`](web/) remains the *product documentation*; the UI is for *your incident data*.)

## Agent runtimes

The workspace is runtime-agnostic. `CLAUDE.md` guides Claude Code; [`AGENTS.md`](AGENTS.md) carries the same contract for Codex CLI, OpenCode, and friends. Launch with:

```bash
scripts/agent.sh             # auto-detects: claude → codex → opencode
scripts/agent.sh claude      # Claude Code — Pro/Max subscription login, or ANTHROPIC_API_KEY in .env
scripts/agent.sh codex       # Codex CLI — ChatGPT subscription login, or OPENAI_API_KEY in .env
```

Subscription logins need no keys in this workspace; API-key billing reads `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` from `.env` (see `.env.example`). Keep approval prompts ON in any runtime — the per-query DB approval is a load-bearing safety layer here, not friction to optimize away.

## RCA Workflow (summary)

1. **Assess** — check Grafana alerts, Jenkins failures, CubeAPM service health
2. **Investigate errors** — search error traces and logs, view trace waterfalls
3. **Check metrics** — error rate, latency percentiles, service uptime
4. **Check deployments** — Jenkins build history, Grafana deploy annotations
5. **Map dependencies** — service dependency graph, datasource health
6. **Correlate** — match deployment timestamps with error spikes across tools

See `CLAUDE.md` for the full agent workflow and exact commands.
