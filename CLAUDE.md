# Reckon (developing this repository)

Reckon is one workspace for running several businesses with a coding agent. Each
business is an org with environments, tool connections, knowledge and saved work.
This file is for working on Reckon's own code. The guide for an agent working
inside an org is [WORKSPACE.md](WORKSPACE.md); the plan is
[docs/PRODUCT-PLAN.md](docs/PRODUCT-PLAN.md).

## Layout

| Path | What it is |
| --- | --- |
| `src/{main,cli,cli_definition}.rs` | The native CLI and command definitions. Everything after the first `--` goes to `run`, `open` or `watch add`. |
| `src/orgs.rs` | `$RECKON_HOME`, org.json, context resolution, folder links, connections, secrets parsing, the per-command child environment |
| `src/connectors.rs` | `reckon run`, `verify`, `login`: argument checks, provider-specific arguments |
| `src/onepassword.rs` | Vault-scoped service accounts, batched secret resolution, save/link/import, recovery and latency measurements |
| `src/workspace.rs` | Each org's agent folder, its generated files and permissions, `reckon open` |
| `src/migrate.rs` | Copies a pre-org checkout's layout into an org |
| `src/{activity,brief,actions}.rs` | The per-org activity log, briefs (one org or all), approved writes |
| `src/{store,investigation,operations,process}.rs` | Sessions, evidence, typed operations, bounded subprocesses |
| `src/assets.rs` | Embedded workspace guide, recipes, skill and knowledge templates |
| `scripts/reckon.py` | Legacy executable alias to `target/release/reckon`; contains no Python |
| `tests/reference/` | Frozen Python reference used by compatibility tests; never part of the runtime |
| `scripts/providers.json` | Every supported tool: binary, setting prefixes, required settings, safety switches, denied arguments, read commands, live check, login |
| `skills/reckon/` | The ops method (investigate, monitor, analyze) and knowledge templates |
| `WORKSPACE.md` | Imported by every org's `CLAUDE.md`; the agent-facing rules and recipes |
| `scripts/setup.sh`, `setup.ps1`, `cli-releases.csv` | Tool installation; pins for the ten sibling CLIs |
| `web/` | Documentation site (Next.js + Fumadocs, static export to Cloudflare) |

## Rules

- **Work directly on `main`.** Do not create feature branches or worktrees.
- **Rust core, one Cargo package.** Keep CLI parsing separate from domain logic. Bash and PowerShell files are thin entry points. The ten tool CLIs stay in Go. Python is for compatibility tests only.
- **No business data in this repo.** Orgs, credentials, knowledge, sessions and incidents live under `$RECKON_HOME` (default `~/.reckon`). Tests and examples use synthetic data only. Old checkouts may still hold ignored `.env.*`, `.config/`, `infra-knowledge/`, `incidents/`: never stage them; add files explicitly.
- **Credentials never reach the agent.** Only `orgs.child_env` puts secrets into a process, and only for one connection. Never print or log a secret value, including in error messages.
- **Reads stay read-only.** A new provider needs its read-only switches in `read_only`, target and auth flags in `deny_args`, local-state commands in `deny_commands`, and either `mode: own` (the CLI enforces read-only itself), `allowlist` (`read_commands`) or `approval` (the agent must ask each time).
- **Fail closed.** Unknown org, environment or connection is an error, never a fallback.

## Adding a tool

1. Read the CLI's source for its environment variables, config path (does it honour `XDG_CONFIG_HOME`?), read-only and no-input switches, update-check opt-outs and auth/target flags.
2. Add an entry to `scripts/providers.json`; pin the release in `scripts/cli-releases.csv` if it is one of ours.
3. Add focused native tests in `tests/native.rs` for its argument checks.
4. Document its recipes in `WORKSPACE.md`.

## Verify

```bash
make check
make test
bash -n scripts/reckon scripts/setup.sh
RECKON_HOME="$(mktemp -d)" target/debug/reckon demo
# Optional: check adopted CLIs' allowed commands against real release binaries (no account needed)
RECKON_ADOPTED_BIN=/path/to/binaries python3 -B -m unittest discover -s tests -p 'test_adopted.py'
```

The web site: `cd web && pnpm install && pnpm types:check && pnpm build:cloudflare && pnpm test:search`.
