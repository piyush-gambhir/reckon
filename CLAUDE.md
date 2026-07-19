# reckon

reckon is an agent workspace for talking to your infrastructure, powered by Grafana, Jenkins, and CubeAPM — plus a read-only ops toolbelt (aws, gh, kcat, rpk, mongosh, psql, mysql, clickhouse client).

## Purpose

This project is a workspace for spinning up a coding agent to investigate production incidents, debug service issues, and perform root cause analysis. The agent has access to twelve read-only CLIs (plus `direnv` and `jq` as supporting tools):

**Observability & CI/CD**
- **Grafana CLI** (`grafana`) -- dashboards, datasources, alerts, annotations
- **Jenkins CLI** (`jenkins`) -- jobs, builds, pipelines, logs, nodes
- **CubeAPM CLI** (`cubeapm`) -- distributed traces, PromQL metrics, LogsQL logs
- **AWS CLI** (`aws`) -- CloudWatch metrics/logs, ALB/ELB, ECS, SQS, RDS, S3 (use when CubeAPM signals are thin or missing)
- **GitHub CLI** (`gh`) -- recent merged PRs, GitHub Actions runs, releases (use to correlate code changes with incident windows)

**Message queues**
- **kcat** -- Kafka metadata + tail messages from a topic (read-only by usage)
- **rpk** -- Kafka consumer-group lag and cluster info (works against vanilla Kafka, MSK, Confluent)

**Kubernetes & cache**
- **kubectl** -- pod/deploy state, events, rollout history (read-only by usage: `get`/`describe`/`logs`/`events`/`top`/`rollout history` only — never `apply`/`delete`/`scale`/`exec`)
- **redis-cli** -- Redis diagnostics (read-only by usage: `INFO`/`SLOWLOG GET`/`LATENCY`/`DBSIZE`/`SCAN` only — never `FLUSH*`/`SET`/`DEL`/`CONFIG SET`)

**Databases (read-only — see Safety section below)**
- **mongosh** -- MongoDB shell (Atlas-compatible)
- **psql** -- PostgreSQL shell
- **mysql** -- MySQL shell
- **ClickHouse** (`clickhouse client`) -- analytics/event tables and system diagnostics. Wired when `CLICKHOUSE_HOST` is set in `.env`; requires a server-side `readonly=1` user profile and `--readonly=1` on every invocation.

**Optional integrations**
- **es** ([es-cli](https://github.com/piyush-gambhir/es-cli)) -- Elasticsearch/ELK: cluster health, index state, Query DSL / SQL search. Only wired when `ES_URL` is set in `.env`. `.envrc` exports `ES_READ_ONLY=true`, which the CLI enforces client-side — mutating commands are refused before any request is sent.

## Environments — read before any query

This workspace targets **three environments: `production`, `staging`, `uat`**, selected by the `RECKON_ENV` variable. Each has its own credentials (`.env.<env>`) and its own CLI config directory (`.config/<env>/`), so no two environments ever share credential state.

**The rule the whole design exists to enforce:** never query one environment while believing you are on another. It is silent, easy, and no tool will catch it.

**Start every task with `./scripts/reckon preflight`.** It reports the active environment, which integrations are genuinely usable, which are not and why, what knowledge exists, and any warnings — in ~8 lines, without touching infrastructure. **Trust it over the toolbelt list below**: this file describes what reckon *can* connect to, preflight describes what *is* connected right now. Discovering a missing credential mid-investigation wastes the whole cascade.

1. **Resolve `RECKON_ENV` before the first query** (preflight prints it). If unset, empty, or uncertain — **stop and ask**. Never infer the environment from service names, hostnames, or context.
2. **State it explicitly** in your first substantive message: *"Working against **production**."* Restate on any switch.
3. **Never mix environments within one investigation, sweep, or analysis.** Cross-environment comparison is legitimate, but label every number with its environment and announce each switch.
4. **Record it in the output** — RCAs carry an `Environment` header row; health reports and analyses name it in the first line.

Switching:

```bash
./scripts/reckon use staging       # persists in .reckon-env, survives new shells
./scripts/reckon status            # confirm what is active and how it resolved
. .\scripts\activate.ps1 -Env staging          # Windows
```

Precedence is `RECKON_ENV` (exported) → `.reckon-env` (file) → `production`. An unrecognised value in **either** fails closed: no credentials load at all.

**direnv is optional.** If it is not hooked into your shell, `.envrc` never runs and nothing warns you — the CLIs silently fall back to saved profiles. `./scripts/reckon doctor` detects exactly this; activate manually with `eval "$(./scripts/reckon env)"`.

**The read-only posture is identical in all three environments** — the split is about *which* infrastructure you touch, never about relaxing safety. But blast radius is not identical: in production, prefer the cheaper signal, bound queries harder, and escalate to a human sooner.

## Database safety contract

The DB clients (`mongosh`, `psql`, `mysql`, and `clickhouse client`) can in principle modify data. The layers below are defence-in-depth; **only layer 1 actually denies writes across every access path**, so it is mandatory, not optional. Before issuing any DB query, confirm:
1. **The DB user in `.env` is a true read-only role.** This is the user's responsibility, not the CLI's, and it is the real write barrier. For ClickHouse, the user MUST have a server-side profile with `readonly=1`. PGOPTIONS/option-files/readPreference/client flags below only narrow the CLI paths; a read-write role can still write through another driver (e.g. `python3 -c` with psycopg2/pymysql/clickhouse-connect) — so refuse, at the approval prompt, any query that opts back into read-write (`SET ... READ WRITE`, `SET default_transaction_read_only=off`, `BEGIN READ WRITE`, or a ClickHouse invocation without `--readonly=1`).
2. **Session-level read-only is applied on every CLI path (Postgres + MySQL + ClickHouse).** `.envrc` sets `PGOPTIONS=-c default_transaction_read_only=on` (libpq honours it for `psql`) and writes a MySQL option file at `$XDG_CONFIG_HOME/mysql/my.cnf` with `init-command=SET SESSION TRANSACTION READ ONLY` (used when you invoke `mysql --defaults-extra-file=...`). Every ClickHouse command MUST include `--readonly=1`; unlike the connection defaults, this is deliberately explicit at each invocation. These block *accidental* writes but do not replace layer 1. Verify: `psql -c "SHOW default_transaction_read_only;"` → `on`; `mysql --defaults-extra-file="$XDG_CONFIG_HOME/mysql/my.cnf" -e "SELECT @@transaction_read_only;"` → `1`; ClickHouse queries include `--readonly=1`.
3. **Mongo URIs carry `?readPreference=secondary`.** This is request *routing*, not authorization — it steers reads to secondaries but does not reject writes. The read-only Atlas role (layer 1) is what prevents writes.
4. **Each `psql` / `mysql` / `mongosh` / `clickhouse` call prompts for permission.** The allowlist deliberately omits these. Don't pre-approve them — read each query before approving. The friction *is* the safety mechanism. (This holds only as long as no broad `Bash(python3 -c ...)` or similar wildcard re-opens a write path around the CLIs — keep the allowlist tight.)
5. **Use `EXPLAIN` / `EXPLAIN ANALYZE` before any non-trivial SELECT.** Production tables can be huge; an unbounded scan is a real outage source even with a read-only role.
6. **Set `LIMIT` on every SELECT.** Default to `LIMIT 100`; raise only when you've seen the row count first.

## Skills

Skills are installed **repo-locally** (the skills CLI honours `XDG_CONFIG_HOME`, which `.envrc` pins into `.config/`; pinned sources live in `skills-lock.json`). The `reckon` skill is the one tracked in this repo at `skills/reckon/`, surfaced to the agent via the `.claude/skills/reckon` → `.agents/skills/reckon` → `skills/reckon` symlink chain — so editing `skills/reckon/SKILL.md` *is* editing the loaded skill. See the `grafana`, `jenkins`, and `cubeapm` skills for full command references and workflows.

## Authentication

This project uses **per-environment, folder-specific credentials** via `.envrc`. It resolves `RECKON_ENV`, sets `XDG_CONFIG_HOME` to `.config/<env>/` within this directory, and loads credentials in this order (later wins): `.env.common` → `.env.<env>` → `.env.<env>.local`. Because each environment gets its own config directory, no two environments ever share CLI credential state. Environment variables are the preferred setup because they make the workspace usable immediately when you `cd` into it, without interactive CLI logins. Saved CLI profiles under `.config/<env>/` remain a fallback option.

An unrecognised `RECKON_ENV` **fails closed** — no credentials are loaded at all, rather than silently falling back to a real environment.

Make sure direnv is set up (`direnv allow`), then populate the credential file for each environment you use:

```bash
cp -n .env.example .env.production      # repeat for .env.staging / .env.uat as needed
$EDITOR .env.production
export RECKON_ENV=production            # or staging / uat
direnv allow

# Optional fallback if you prefer saved profiles:
# grafana login
# jenkins login
# cubeapm login
# aws configure                # writes .config/<env>/aws/{config,credentials}
# gh auth login                # writes .config/<env>/gh/hosts.yml
```

`grafana`, `jenkins`, `cubeapm`, and `gh` honour `XDG_CONFIG_HOME` so their state lands inside `.config/<env>/` automatically. `aws` does not, so `.envrc` also exports `AWS_CONFIG_FILE` and `AWS_SHARED_CREDENTIALS_FILE` to point inside the active environment's directory. Kafka and DB tools (`kcat`, `rpk`, `mongosh`, `psql`, `mysql`, and `clickhouse client`) read credentials directly from env vars in `.env.<env>`.

Config files are stored at:
- `.config/<env>/grafana-cli/config.yaml`
- `.config/<env>/jenkins-cli/config.yaml`
- `.config/<env>/cubeapm-cli/config.yaml`
- `.config/<env>/aws/{config,credentials}`
- `.config/<env>/gh/{config.yml,hosts.yml}`

Verify every connection with one command — it runs exactly one safe read per
configured integration, bounded by a timeout, and reports a pass/fail table:

```bash
./scripts/reckon verify              # all configured integrations
./scripts/reckon verify grafana      # just one
```

This is the only `reckon` subcommand that contacts your infrastructure; `status`,
`doctor`, and `preflight` are purely local inspection. The individual commands it
runs are listed per-tool in the sections below.

## RCA Workflow

For any infrastructure work, invoke the **`reckon` skill** and enter the mode that fits the request:

| Mode | Enter when | Produces |
|---|---|---|
| **investigate** | A specific bad thing in a specific window — alert fired, metric dropped, errors spiked, deploy suspected | An RCA at `incidents/<YYYY-MM-DD>-<slug>/` |
| **monitor** | Nothing known broken; sweeping — health check, pre/post-deploy verification, drift hunting | A health report |
| **analyze** | No fault — capacity, cost, performance profiling, growth | An analysis writeup |

Always consult `infra-knowledge/` before querying anything. It is **per-environment**: read `infra-knowledge/_shared/` first, then overlay `infra-knowledge/$RECKON_ENV/`, where the environment-specific file always wins.

Quick reference for the investigate mode (the skill expands on each step):

### 1. Assess the situation
- Check **Grafana alerts**: `grafana alert rule list -o json` and `grafana alert silence list -o json`
- Check **Jenkins build status**: `jenkins job list --recursive --status FAILURE -o json`
- List **CubeAPM services**: `cubeapm metrics label-values service -o json` (use this, not `cubeapm traces services`, as the canonical inventory — see [`infra-knowledge/<env>/server-quirks.md`](infra-knowledge/<env>/server-quirks.md) for any deployment-specific behaviour)

### 2. Investigate errors (traces + logs)
- Search error traces: `cubeapm traces search --service <svc> --status error --last 1h -o json`
- Get trace waterfall: `cubeapm traces get <trace-id> -o json`
- Search related logs: `cubeapm logs query 'error' --service <svc> --last 1h -o json`
- Check log volume: `cubeapm logs hits --query 'level:error' --last 6h --step 15m -o json`

### 2b. Search an Elasticsearch/ELK log store (es) — when a service logs to ES, not CubeAPM
Use when `cubeapm logs hits` stays zero across a wide window and the service's log destination is Elasticsearch (see `infra-knowledge/<env>/known-issues.md` "Logs not shipped to CubeAPM"). Read-only is enforced client-side via `ES_READ_ONLY=true` from `.envrc`.

- Cluster sanity: `es cluster health -o json`
- Find the right index: `es index list -o json | jq -r '.[].index' | grep -i <svc>`
- Count errors in a window (query body via stdin with `-f -`):
  ```bash
  echo '{"query":{"bool":{"must":[{"match":{"level":"error"}},{"range":{"@timestamp":{"gte":"<ISO>","lte":"<ISO>"}}}]}}}' \
    | es search count <index> -f - -o json
  ```
- Search (Query DSL, same body shape): `es search query <index> -f - --size 20 --sort '@timestamp:desc' -o json`
- Or SQL when simpler: `es search sql --query 'SELECT "@timestamp", message FROM "<index>" WHERE message LIKE '\''%error%'\'' ORDER BY "@timestamp" DESC LIMIT 20' -o json`

### 2c. Query ClickHouse analytics/event data
Use this database connection when `CLICKHOUSE_HOST` is set and the incident points to analytics or event tables, ClickHouse ingestion volume, or slow ClickHouse queries. The real write barrier is the user's server-side `readonly=1` profile; the client-side barrier is `--readonly=1` on every invocation, and `clickhouse` is deliberately not pre-approved.

Use this command shape for every query (add `--database "$CLICKHOUSE_DATABASE"` when the optional database is set):
```bash
clickhouse client --host "$CLICKHOUSE_HOST" --port "$CLICKHOUSE_PORT" --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --secure --readonly=1 --query '<SQL>'
```

Run `EXPLAIN indexes = 1` before every non-trivial `SELECT`, then run the bounded query. Every `SELECT`, including system-table diagnostics, MUST have a `LIMIT` (default `LIMIT 100`).

- Discover analytics/event tables: `SELECT database, name, engine, total_rows, total_bytes FROM system.tables WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA') ORDER BY total_rows DESC LIMIT 100`
- Inspect recent slow queries in `system.query_log`: `SELECT event_time, query_duration_ms, read_rows, read_bytes, query FROM system.query_log WHERE type = 'QueryFinish' AND event_time >= now() - INTERVAL 1 HOUR ORDER BY query_duration_ms DESC LIMIT 100`
- Check current ingestion activity in `system.metrics`: `SELECT metric, value FROM system.metrics WHERE metric ILIKE '%Insert%' ORDER BY metric LIMIT 100`
- Check cumulative ingestion volume in `system.events`: `SELECT event, value FROM system.events WHERE event IN ('InsertedRows', 'InsertedBytes') ORDER BY event LIMIT 100`
- Sample a narrowed event table only after `EXPLAIN`: `SELECT * FROM <database>.<event_table> WHERE <timestamp_column> >= '<from-ISO>' AND <timestamp_column> <= '<to-ISO>' ORDER BY <timestamp_column> DESC LIMIT 100`

### 3. Check metrics
- Error rate: `cubeapm metrics query 'rate(http_requests_total{status=~"5.."}[5m])' -o json`
- Latency: `cubeapm metrics query 'histogram_quantile(0.99, sum by (le) (rate(http_duration_seconds_bucket[5m])))' -o json`
- Service health: `cubeapm metrics query 'up' -o json`

### 4. Check recent deployments
- Grafana annotations: `grafana annotation list --tags deploy -o json`
- Jenkins recent builds: `jenkins build list <deploy-job> -o json`
- Build logs: `jenkins build log <job> <number>`

### 4b. Cross-check code & deploy context (gh)
Use when a service deploys via GitHub Actions (not Jenkins), or to read the *intent* behind a recent change.
- Recent merges around the window: `gh pr list --search "merged:>=<ISO> closed" --json number,title,mergedAt,author,url`
- One PR's intent + diff scope: `gh pr view <num> --json title,body,files,mergedAt`
- Failing GitHub Actions runs: `gh run list --workflow <wf> --status failure --json status,conclusion,createdAt,url`
- Recent releases: `gh release list --limit 10 --json tagName,publishedAt,name`

### 4c. Cross-check infra signals (aws) — when CubeAPM signals are thin
Use when CubeAPM data is missing, lagging, or attribution is weak (e.g. `host.name=UNSET`, uninstrumented callers, or the documented ~5–20 min log ingestion lag).
- ALB 5xx by target group: `aws cloudwatch get-metric-statistics --namespace AWS/ApplicationELB --metric-name HTTPCode_Target_5XX_Count --start-time <ISO> --end-time <ISO> --period 60 --statistics Sum --output json`
- SQS queue depth: `aws sqs get-queue-attributes --queue-url <url> --attribute-names ApproximateNumberOfMessages,ApproximateAgeOfOldestMessage --output json`
- ECS task events / restarts: `aws ecs describe-services --cluster <c> --services <s> --output json | jq '.services[].events[:20]'`
- CloudWatch logs (when CubeAPM logs lag): `aws logs filter-log-events --log-group-name <lg> --start-time <epoch-ms> --end-time <epoch-ms> --filter-pattern '"ERROR"' --output json`

### 5. Check service dependencies
- Dependency graph: `cubeapm traces dependencies --last 1h -o json`
- Grafana datasource health: `grafana datasource list -o json`

### 5b. Inspect message queues (Kafka — kcat + rpk)
Use when the symptom is "consumer is slow / lagging / stuck", a deadletter topic is suspected, or a service that talks to Kafka is misbehaving. The team's stack uses many consumer groups — see [`infra-knowledge/_shared/service-name-mapping.md`](infra-knowledge/_shared/service-name-mapping.md) for the mapping from service to consumer group.

**Kafka safety contract** (kcat/rpk have no read-only mode — these rules are the only client-side guard):
1. **The SASL principal in `.env` should be read-only at the broker** — ACLs limited to `Describe`/`Read` (for MSK IAM: `kafka-cluster:Connect`, `Describe*`, `ReadData` — no `WriteData`/`Create*`/`Delete*`/`Alter*`). That's the real enforcement; provision it like the read-only DB role.
2. **Read commands only**: `kcat -L` / `-C` / `-Q`, `rpk cluster info`, `rpk topic list/describe/consume`, `rpk group list/describe`. **Never** `kcat -P`, `rpk topic produce/create/delete/alter-config`, or `rpk group delete/seek`.
3. **Never join a production consumer group.** Reading with `kcat -G <group>` or `rpk topic consume -g <group>` *joins* the group, triggering a rebalance of the real consumers and committing offsets — production impact from a "read". Always consume group-less (plain `kcat -C`, `rpk topic consume` without `-g`), which reads partitions directly with no side effects.
4. **Keep the allowlist read-shaped**: pre-approve only the read commands above; let anything else prompt.

- Cluster + topic metadata (`kcat`):
  ```bash
  kcat -L -b "$KAFKA_BOOTSTRAP_SERVERS" -X security.protocol=$KAFKA_SECURITY_PROTOCOL \
       -X sasl.mechanism=$KAFKA_SASL_MECHANISM -X sasl.username=$KAFKA_SASL_USERNAME \
       -X sasl.password=$KAFKA_SASL_PASSWORD -t <topic> -J | jq
  ```
- Tail last N messages from a topic (great for inspecting deadletter / error topics):
  ```bash
  kcat -C -b "$KAFKA_BOOTSTRAP_SERVERS" -X security.protocol=$KAFKA_SECURITY_PROTOCOL \
       -X sasl.mechanism=$KAFKA_SASL_MECHANISM -X sasl.username=$KAFKA_SASL_USERNAME \
       -X sasl.password=$KAFKA_SASL_PASSWORD -t <topic> -o -10 -e -q
  ```
- Consumer-group lag (`rpk` — primary lag command):
  ```bash
  rpk group describe <consumer-group> --brokers "$KAFKA_BOOTSTRAP_SERVERS"
  ```
- All groups + their state:
  ```bash
  rpk group list --brokers "$KAFKA_BOOTSTRAP_SERVERS"
  ```
- Topic partition assignment + leadership:
  ```bash
  rpk topic describe <topic> --brokers "$KAFKA_BOOTSTRAP_SERVERS"
  ```

### 5c. Inspect database state (MongoDB / Postgres / MySQL)
Use sparingly and only after observability tools have narrowed the suspect to a specific table, collection, or query. **Re-read the "Database safety contract" above before issuing any query.** Always `EXPLAIN` first; always `LIMIT`.

**MongoDB (Atlas) — `mongosh`:**
- Liveness: `mongosh "$MONGODB_URI" --eval 'db.runCommand({ping:1})'`
- Currently-running ops (find slow query): `mongosh "$MONGODB_URI" --eval 'db.currentOp({active:true, secs_running:{$gt:1}})'`
- Replica-set status: `mongosh "$MONGODB_URI" --eval 'rs.status()'`
- Server-level counters: `mongosh "$MONGODB_URI" --eval 'db.serverStatus().connections'`
- Collection sample (always `LIMIT`): `mongosh "$MONGODB_URI" --eval 'db.<coll>.find({<filter>}).limit(10).toArray()'`

**PostgreSQL — `psql`:**
- Confirm read-only is on: `psql -c "SHOW default_transaction_read_only;"` (must be `on`)
- Active queries + blocking: `psql -c "SELECT pid, state, wait_event_type, wait_event, now()-query_start AS dur, query FROM pg_stat_activity WHERE state != 'idle' ORDER BY dur DESC LIMIT 20;"`
- Locks: `psql -c "SELECT relation::regclass, mode, granted, pid FROM pg_locks WHERE NOT granted;"`
- Slow plan: `psql -c "EXPLAIN (ANALYZE, BUFFERS) <query>;"` *(EXPLAIN ANALYZE actually runs the query — always confirm read-only intent first)*
- Replication lag: `psql -c "SELECT client_addr, state, sync_state, pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS lag_bytes FROM pg_stat_replication;"`

**MySQL — `mysql`:**
> Always invoke the client as `mysql --defaults-extra-file="$XDG_CONFIG_HOME/mysql/my.cnf" ...`. That option file (written by `.envrc`/`activate.ps1`) supplies the read-only `init-command`, user, and database — the mysql client has **no** `MYSQL_INIT_COMMAND`/`MYSQL_USER`/`MYSQL_DATABASE` env var, so a bare `mysql` would neither apply session read-only nor connect as the intended role. The commands below omit the flag for brevity; include it every time.
- Confirm read-only is on: `mysql -e "SELECT @@transaction_read_only;"` (must be `1`)
- Active sessions / slow queries: `mysql -e "SELECT id, user, host, db, command, time, state, LEFT(info, 200) AS query FROM information_schema.processlist WHERE command != 'Sleep' ORDER BY time DESC LIMIT 20;"`
- InnoDB engine status (long): `mysql -e "SHOW ENGINE INNODB STATUS\G"`
- Slow plan: `mysql -e "EXPLAIN <query>;"`
- Replication: `mysql -e "SHOW REPLICA STATUS\G"`

### 5d. Inspect Kubernetes state (kubectl) — read-only by usage
Use when the symptom looks like pod churn, a deploy blackout, OOM kills, or replica-count anomalies (e.g. "service emitted 0 metrics and 0 logs for 80 minutes" — check whether anything was *running*). `KUBECONFIG` is workspace-local (`.config/<env>/kube/config`); seed it with `aws eks update-kubeconfig --name <cluster> --region "$AWS_REGION"`.

- Pod state + restarts: `kubectl get pods -n <ns> -o wide`
- Why a pod died: `kubectl describe pod <pod> -n <ns>` (look at Events, LastState, OOMKilled)
- Recent cluster events: `kubectl get events -n <ns> --sort-by=.lastTimestamp | tail -30`
- Deploy history + replica timeline: `kubectl rollout history deploy/<d> -n <ns>` and `kubectl get deploy <d> -n <ns> -o json | jq '.status'`
- Resource pressure: `kubectl top pods -n <ns>` (needs metrics-server)

**Never** `apply`, `delete`, `scale`, `edit`, `exec`, or `port-forward` — this workspace observes; humans mitigate.

### 5e. Inspect Redis (redis-cli) — read-only by usage
Use when a cache/queue backed by Redis is suspect (the Grafana datasource list includes Redis instances). Connect with `redis-cli -u "$REDIS_URL"`.

- Health + memory: `redis-cli -u "$REDIS_URL" INFO memory` (also `INFO stats`, `INFO clients`)
- Slow commands: `redis-cli -u "$REDIS_URL" SLOWLOG GET 20`
- Latency history: `redis-cli -u "$REDIS_URL" LATENCY LATEST`
- Keyspace size: `redis-cli -u "$REDIS_URL" DBSIZE` (use `SCAN`, never `KEYS *`, on production)

**Never** `FLUSHALL`/`FLUSHDB`, `SET`/`DEL`/`EXPIRE`, or `CONFIG SET`.

### 6. Correlate across tools
- Match deployment timestamps (Jenkins build times) with error spikes (CubeAPM metrics/traces)
- Check Grafana annotations around the incident window
- Trace error propagation through the dependency graph

## Agent Guidelines

- **ALWAYS use `-o json`** for all CLI commands when parsing output programmatically.
- **Start broad, then narrow.** List services first, then drill into the problematic one.
- **Correlate timestamps.** Match deploy times from Jenkins with error spikes in CubeAPM.
- **Use time ranges.** Most CubeAPM commands accept `--last 1h`, `--from`, `--to`.
- **Check multiple signals.** Don't rely on just traces or just metrics -- cross-reference.
- **Document findings.** As you investigate, summarize what you find at each step.
- **Be explicit about uncertainty.** If the data is inconclusive, say so.
- **Persist the RCA in the incidents convention.** Write all output to `incidents/<YYYY-MM-DD>-<slug>/` (UTC date the incident started; `RCA.md` + `alert.txt` + `learnings.md` + `evidence/`) per [`skills/reckon/references/incidents-convention.md`](skills/reckon/references/incidents-convention.md) and the skill's §7 — never to the repo root.
