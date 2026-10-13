# Reckon product plan

Updated 2026-10-14. This replaces the incident-only plan of 2026-09-08. The
incident investigation work it described is now the **ops** area of a wider
product; its unfinished items (evidence contract, replay evaluations) are paused,
not dropped.

## What Reckon is

Reckon is one workspace for running several businesses with a coding agent.
Each business is an **org**. Each org has its own environments, tool
connections, company knowledge and saved work. You open an org, and Claude Code
or Codex works on that business only: development, product, GTM, sales and ops,
through the CLIs you already use.

Decisions taken on 2026-10-13:

- **Solo operator first.** One person, many orgs. No team accounts or roles yet.
- **Agent workspace.** Claude Code or Codex runs in a folder; Reckon is the
  `reckon` command. Not a web app.
- **Writes need approval.** Reads run freely. Every write is previewed and
  approved by the operator; production writes get an extra guard.
- **Same repo and name.** Incident investigation stays, as the ops area.

## The model

| Concept | Meaning |
| --- | --- |
| Org | One business. Has a name, a list of environments, connections, knowledge and work. |
| Environment | Per org, for example `prod`, `uat`, `dev`. One is marked production. |
| Connection | One configured account of one tool: a Jenkins server, a CubeAPM tenant, a Search Console login. |
| Context | The org and environment an agent works in. Pinned when the agent starts. |
| Workspace | The folder the agent runs in for an org. Holds knowledge and saved work. |

A connection is either tied to one environment or shared by all of them:

```json
"connections": {
  "jenkins-prod": {"provider": "jenkins", "env": "prod"},
  "jenkins-uat":  {"provider": "jenkins", "env": "uat"},
  "cubeapm":      {"provider": "cubeapm", "env_labels": {"prod": "production", "uat": "uat"}},
  "gsc":          {"provider": "gsc"}
}
```

Jenkins runs separate servers, so each environment has its own connection.
CubeAPM is one server holding every environment, so one connection carries a
label per environment and Reckon applies the active one. Search Console has no
environments at all. When a command names a provider, Reckon picks the only
connection that fits the active environment and fails if there is none or more
than one.

## Where data lives

All business data lives under `$RECKON_HOME` (default `~/.reckon`), never in the
code checkout:

```
~/.reckon/
  context                     default org/env for the next launch
  orgs/<org>/
    org.json                  environments and connections, no secrets
    secrets/<connection>.env  credentials, 0600, never shown to the agent
    config/<connection>/      the CLI's own config and saved logins
    runtime/<connection>/     private HOME, cache and temp for that CLI
    workspace/                the agent's folder for this org
      RECKON.md               generated: environments and connections
      CLAUDE.md, AGENTS.md    created once: import RECKON.md and WORKSPACE.md; your notes
      .claude/, .agents/      skills and permission settings
      knowledge/              company and per-environment facts
      sessions/               saved work with evidence
      incidents/              promoted RCAs
```

A separate workspace folder per org matters for more than tidiness. Claude Code
and Codex key their memory, conversation history and project settings by
folder, so one folder per org keeps one business's facts out of another's
conversations and lets `claude --continue` resume the right business.

## Safety rules

1. **The agent never holds credentials.** `reckon open` starts the agent with
   the context pinned and provider credentials removed. `reckon run` loads one
   connection's credentials into one command and nothing else.
2. **Reads are read-only by construction.** Reckon forces each CLI's read-only
   and no-prompt switches and rejects arguments that would change the target or
   turn read-only off. Tools without a read-only switch allow only listed read
   commands.
3. **Context cannot drift.** A running agent keeps the context it started with.
   Sessions record their org, environment and connections, and refuse evidence
   from any other.
4. **Writes are prepared, then applied by the operator** (milestone 3). The
   agent prepares an action with its exact payload and real target; the
   operator runs `reckon act apply <id>`, which records a receipt (done, failed
   or unknown).

Limits, stated plainly: everything runs as the same OS user, so these rules stop
mistakes, not a hostile process. Server-side read-only roles remain the real
write barrier for databases.

## Sessions and data

- **Sessions** hold any piece of work with its evidence: an incident
  investigation (service and time window) or a task in any area ("weekly search
  review", "release check"). Evidence comes from typed collection or from any
  `reckon run --session` read, with the command, connection, time and hashes
  recorded.
- **Notes** record findings, hypotheses, rejected ideas and questions, tied to
  evidence. Reports, history, resume and promotion to incidents work as before,
  per org.
- **Knowledge** is per org: `_shared/` plus one folder per environment.
- **Agent conversations** stay per org because each org has its own workspace
  folder.
- **Action receipts** (milestone 3) record every write and its outcome.

## Milestones

| | Milestone | Done when |
| --- | --- | --- |
| M1 | Orgs, environments and connections (built 2026-10-14) | Two orgs work side by side. All nine CLIs plus `gh`, `aws`, `kubectl` and `git` run through `reckon run` and `reckon verify` with per-connection credentials. Sessions are per org. The current single-org data migrates with one command. Missing credentials fail with no fallback to global logins. |
| M2 | First cross-area workflows | A GTM review (Search Console, Bing, Clarity) and a release review (Jenkins, GitHub) run as saved sessions with evidence. Each org has a company profile. Read-only MCP for tools without a CLI. |
| M3 | Approved writes | Prepare and apply, receipts, the production guard, a first write (create a GitHub or Jira issue). |
| M4 | Dogfood and measure | Used on two real businesses for two weeks; the next connectors and workflows chosen from what was actually repeated. |

## Deferred

Team accounts, a hosted or scheduled agent, MCP writes, a web dashboard and
autonomous remediation. The Python 3.9 standard-library core stays.
