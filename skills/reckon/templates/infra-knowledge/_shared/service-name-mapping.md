# Service-name mapping (CubeAPM ↔ Grafana ↔ Jenkins ↔ Kafka ↔ K8s)

The same logical service goes by different names in each tool. RCAs lose minutes
hunting these down. This file is the lookup so you copy a name in one tool and
find its equivalents fast. Copy this file to `service-name-mapping.md` and fill
in your team's real names.

> **Conventions for adding a row.** Use the *exact* string the tool stores
> (case, separators, prefixes). When a name is unknown for a tool, write `?` —
> never guess; replace it when you find it. When the tool genuinely has no entry
> (e.g. the service doesn't ship logs to CubeAPM), write `—` so the next on-call
> doesn't re-search.

---

## <stack name> stack

A short note on anything weird about this stack's naming (e.g. underscore vs
hyphen drift between the main service and its workers).

| Surface          | CubeAPM `service` label | CubeAPM logs `service.name` | Grafana folder / dashboard | Grafana alert rules | Kafka consumer group(s) | K8s / hosts (from logs) | Jenkins job |
|------------------|-------------------------|------------------------------|----------------------------|---------------------|-------------------------|-------------------------|-------------|
| Main service     | `EXAMPLE_SERVICE`       | `EXAMPLE_SERVICE`            | `<folder uid / name>`      | `<rule group>`      | `example-consumer-group`| `<host pattern>`        | `<job>`     |
| CRON sub-service | `EXAMPLE-SERVICE-CRON`  | `?`                          | `?`                        | `?`                 | `?`                     | `?`                     | `?`         |
| Worker label     | `EXAMPLE-SERVICE-WORKER`| `?`                          | `?`                        | `?`                 | `?`                     | `?`                     | `?`         |

> **⚠️ Naming drift example** — record the exact spellings so nobody transliterates:
> - Main service uses underscore: `EXAMPLE_SERVICE`
> - Sub-services use hyphens: `EXAMPLE-SERVICE-CRON`
> - Grafana alert rule group is capitalized: `Example`
> - **Always copy the exact string** from each tool.

### Kafka topics owned/consumed by this stack

| Topic                  | Producer (or origin) | Consumer group           |
|------------------------|----------------------|--------------------------|
| `example_topic`        | `EXAMPLE_SERVICE`    | `example-consumer-group` |

---

## Datasource UIDs in this Grafana

Stable handles you can reference in `grafana datasource query <uid> --expr ...`
calls (the only way the Grafana CLI queries Prometheus/Loki backends directly).

| Name         | UID                | Type         | URL / notes                          |
|--------------|--------------------|--------------|--------------------------------------|
| `<prom DS>`  | `<uid>`            | `prometheus` | `<url>` — which alerts query this?   |
| `<loki DS>`  | `<uid>`            | `loki`       | `<url>` — log label conventions here |

---

## How to grow this file

When you finish an RCA, take 30 seconds and add any *new* CubeAPM service label,
Grafana alert rule, Kafka topic/consumer-group, K8s namespace, or Jenkins job
that wasn't already here. The cost is small; the saved time next incident is large.

If a service spans multiple rows (sync + cron + worker), keep them separate — the
lifecycle and host shape diverge.
