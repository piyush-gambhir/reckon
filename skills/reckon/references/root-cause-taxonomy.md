# Root-cause taxonomy

A controlled vocabulary for the **`root_cause_category`** field every RCA carries (see
[`rca-doc-template.md`](rca-doc-template.md) header table + Diagnosis block). One incident gets
**exactly one** category — the label that points at the *failing subsystem*, not a coarse bucket.

## Why a fixed vocabulary

Free-text headlines don't accumulate. A stable category makes the `incidents/` corpus
*classifiable and searchable*: `grep -rl 'root_cause_category: connection_pool_leak' incidents/`
surfaces every past pool-leak in seconds, and a recurring category across services is itself a
finding (a platform-wide pattern the free-text headlines hide). Prefer the **narrowest** category
that the evidence supports — `missing_index` over `cpu_saturation_bad_query` over the generic
`resource_exhaustion` — because a narrow label points a reader straight at the fix.

If nothing fits, use a `generic_fallback` category and say so in the confidence rationale — do not
invent a new name inline. Propose additions at the bottom of this file in a PR instead, so the
vocabulary stays a single source of truth.

## The taxonomy

Grouped by subsystem. `group` is only for organizing this list and the self-consistency check
below — the RCA records the leaf `name`.

### Database — connection layer (`database`)
Postgres · MySQL · MongoDB · ClickHouse · Redis.

| name | when it fits |
|---|---|
| `connection_exhaustion` | `max_connections` ceiling hit; new sessions rejected. |
| `connection_pool_leak` | App pool acquires faster than it releases; checkout waits climb. |
| `pool_checkout_starvation` | A few slow ops hold the pool; *every* op on that client jumps to the same multi-second latency (SKILL.md pitfall #17). |
| `idle_in_transaction_session_leak` | Sessions stuck idle-in-transaction hold locks and slots. |
| `max_connections_misconfigured` | Server-side `max_connections` too low for current load shape. |

### Database — compute & query (`database`)
| name | when it fits |
|---|---|
| `cpu_saturation_bad_query` | One hot SQL pattern (missing index, full scan) saturates CPU. |
| `cpu_saturation_workload_burst` | Aggregate load saturates CPU with no single dominant query. |
| `missing_index` | Selective index absent; sequential scans dominate IO/CPU. |
| `query_plan_regression` | Planner choice changed (stats/param/version) and degraded. |
| `stale_statistics` | Outdated planner stats produce inefficient plans. |
| `lock_contention` | Row/table locks queue; queries wait without saturating compute. |
| `deadlock_storm` | Mutually-blocking transactions abort and retry in a loop. |

### Database — storage, maintenance, replication (`database`)
| name | when it fits |
|---|---|
| `storage_exhaustion` | Data/WAL volume full; writes fail or DB refuses connections. |
| `storage_iops_throttling` | Provisioned IOPS ceiling hit; every IO queues (RDS/EBS). |
| `storage_burst_balance_depleted` | Burst-credit / gp2 balance drained; latency steps up cliff-edge. |
| `replication_lag` | Replica falls behind primary (WAL volume, long replica query, undersized replica). |
| `failover_event` | Primary failover / election; brief unavailability + connection resets. |
| `table_bloat` | Dead tuples / fragmentation inflate scan cost over time. |

### Cache & queue (`data_and_pipeline`)
Redis · Kafka (kcat/rpk).

| name | when it fits |
|---|---|
| `cache_eviction_storm` | Redis memory pressure evicts hot keys; downstream DB load spikes. |
| `kafka_consumer_lag` | Consumer group can't keep up; lag grows unbounded. |
| `kafka_rebalance_storm` | Group repeatedly rebalances; consumption stalls. |
| `deadletter_backlog` | Errors accumulate on a DLQ / retry topic. |
| `data_late_arrival` | Upstream events arrive late; windows compute on incomplete data. |
| `data_volume_anomaly` | Ingest volume spikes/drops abnormally (e.g. ClickHouse insert burst). |
| `data_schema_drift` | Producer changed shape; consumers fail to deserialize. |

### Kubernetes & workload (`kubernetes_workload`)
| name | when it fits |
|---|---|
| `pod_oomkilled` | Container exceeded memory limit; kernel OOM-killed it. |
| `pod_cpu_throttled` | CPU limit throttling inflates latency without crashes. |
| `pod_crashloop_backoff` | Pod restarts in a loop; never reaches Ready. |
| `pod_imagepull_backoff` | Image pull fails (tag/registry/creds); pod never starts. |
| `pod_evicted_node_pressure` | Node memory/disk pressure evicts pods. |
| `pod_pending_insufficient_resources` | No node has room; pods stay Pending. |
| `readiness_probe_misconfigured` | Probe flaps healthy pods in/out of the endpoint set. |
| `deployment_rollout_stuck` | Rollout wedged; old + new replicas coexist or none go Ready. |
| `hpa_misconfiguration` | Autoscaler scales the wrong way or not at all. |
| `zero_replicas_running` | Nothing was actually running (SKILL.md: "was anything running?"). |

### Network & DNS (`network_and_dns`)
| name | when it fits |
|---|---|
| `dns_resolution_failure` | Name resolution fails/slows; connections error before connect. |
| `tls_certificate_expired` | Cert expiry breaks TLS handshakes. |
| `load_balancer_unhealthy_targets` | ALB/ELB pulls targets out; 5xx or connection resets at the edge. |
| `security_group_misconfiguration` | SG / firewall rule blocks a required path. |
| `network_partition` | Connectivity lost between components (AZ link, overlay, NAT). |
| `nat_gateway_throttling` | NAT gateway port/throughput ceiling throttles egress. |

### Cloud & infrastructure (`infrastructure`)
AWS.

| name | when it fits |
|---|---|
| `az_outage` | Single-AZ impairment. |
| `region_outage` | Region-wide provider impairment. |
| `cloud_provider_event` | Named provider incident affecting a managed service. |
| `service_quota_exceeded` | An AWS service/account quota hit. |
| `iam_policy_misconfiguration` | Missing/overtight IAM permission breaks a call path. |
| `lambda_concurrent_executions_exceeded` | Lambda concurrency ceiling throttles invocations. |

### External / upstream dependency (`external_dependency`)
| name | when it fits |
|---|---|
| `upstream_service_outage` | A depended-on internal/third-party service is down. |
| `upstream_rate_limit` | A dependency started rate-limiting us. |
| `upstream_schema_change` | A dependency changed its contract/response shape. |
| `upstream_authentication_failure` | Auth to a dependency broke (expired token/key). |
| `third_party_breaking_change` | A vendor shipped a breaking change. |

### Code, config & deploy (`code_and_configuration`)
| name | when it fits |
|---|---|
| `bad_deploy` | A specific release introduced the regression (git diff on the hot path). |
| `feature_flag_misconfiguration` | A flag flip changed behavior/load. |
| `env_var_missing` / `env_var_misconfiguration` | Missing or wrong environment value. |
| `secret_missing` / `secret_rotation_failure` | Absent or mid-rotation credential. |
| `code_defect_null_handling` | Null/absent-field handling bug. |
| `code_defect_concurrency_bug` | Race / lock-ordering / non-atomic update. |
| `code_defect_resource_leak` | App leaks connections/handles/memory over time. |
| `fan_out_amplification` | One request triggers super-linear downstream calls/writes. |

### Workload & traffic (`workload_and_traffic`)
| name | when it fits |
|---|---|
| `application_tier_load_spike` | Legitimate load rose past headroom. |
| `traffic_burst_unprotected` | A burst with no rate limit / backpressure saturated a tier. |
| `bulk_admin_action` | An admin/bulk endpoint or job pushed a one-off load spike. |
| `abusive_traffic` / `ddos_event` | Hostile traffic pattern. |
| `cascading_failure` | One component's failure propagated through dependents. |

### Generic fallback (`generic_fallback`)
Use only when no narrower category fits; explain why in the confidence rationale.

| name | when it fits |
|---|---|
| `resource_exhaustion` | Something ran out (CPU/mem/IO/conns) and evidence can't localize which. |
| `configuration_error` | A misconfiguration you can't attribute to a sharper category above. |
| `unknown` | Root cause genuinely undetermined — pair with a low confidence score. |
| `healthy` | Investigated and found no real incident (noise / already-recovered / mis-scoped alert). |

## Self-consistency check (borrowed from OpenSRE's category-alignment lint)

Before finalizing, **verify the category matches your prose.** OpenSRE lowers its confidence score
automatically when the root-cause text signals a different subsystem than the chosen category
(e.g. the prose is full of `connection pool` / `replication lag` but the category is a Kubernetes
one). Do the same by hand:

1. Read your §1 summary and §6 top root cause.
2. If the prose's dominant subsystem keywords disagree with the category's group above, either the
   category is wrong or the prose is describing a *symptom* one level down from the true cause.
   Resolve it — don't ship the mismatch.
3. If you keep the category despite surface-level keyword disagreement (legitimate when the cause is
   upstream of the symptom — e.g. a `bad_deploy` that manifests as `connection_pool_leak`
   downstream), **subtract ~0.15 from the confidence score** and state the tension in one line, so a
   reader knows the classification was a judgment call.

## Proposed additions (PR here, don't inline in an RCA)

<!-- Add rows as new categories earn their place. Cite the incident folder that motivated each. -->
- _(none yet)_
