# Service inventory

Canonical service label values (the value seen in CubeAPM's `service` metric label), what each service does, and team ownership. Copy this file to `services.md` and replace the examples with your real inventory.

Keep this list in sync with `cubeapm metrics label-values service`. If a service appears there but not here, add it; if it appears here but hasn't reported metrics in 7+ days, remove it or mark it deprecated.

## Conventions

- **Label source of truth:** CubeAPM metric label `service` (not `service.name`, which is used in logs and sometimes differs in case).
- **Case matters.** `My-Service` and `my-service` are different label values.
- **Environment label:** `env` with values from [metric-conventions.md](metric-conventions.md). Treat missing/empty env values as a data-quality bug.

## Services

Last refreshed: YYYY-MM-DD.

| Service label       | Owns                                      | Team       | Notes |
|---------------------|-------------------------------------------|------------|-------|
| `example-api`       | Public API gateway                        | Platform   | Frequent downstream of user-facing flows |
| `example-worker`    | Async job runner                          | Platform   | Watch queue lag |
| `example-frontend`  | Web UI                                    | Product    |       |
| *(add yours)*       |                                           |            |       |

## Multi-service relationships (observed cascades)

Document known fan-out patterns. Example format:

- `example-api` `POST /do/thing` → synchronous fan-out to `example-worker`, `example-downstream`. A slowdown in either cascades to this endpoint.
- `example-api` `POST /bulk/import` → generates sustained `example-worker` load for several minutes per call. See [bulk-endpoints.md](bulk-endpoints.md).

## Time zone

Record the team's operating time zone here. Alerts and dashboards often display local time; CubeAPM and Grafana store UTC. Convert consistently in RCA docs — include both.

Example: `IST (UTC+5:30)` / `PST (UTC-8, UTC-7 in DST)`.
