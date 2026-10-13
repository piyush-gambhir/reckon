# Workflow: release review

What shipped to an environment in a window, whether it is healthy, and what still
needs attention. Uses `jenkins` and/or `github`, plus `cubeapm` or `grafana` for
health. Run it in the environment being reviewed; another environment needs another agent.

## Start

```bash
reckon task --question "Release review <env>, <from> to <to>" --area dev --from <ISO> --to <ISO>
```

Read `knowledge/<env>/deploy-pipelines.md` for each service's deploy job and branch.

## Reads

1. Deploys: `reckon run jenkins --session <id> -- build list <deploy-job> --limit 25 -o json`
   (filter by the returned timestamps), or
   `reckon run github --session <id> -- run list --repo <owner/repo> --workflow <wf> --json databaseId,headSha,status,conclusion,createdAt,url`.
2. What each deploy contained: `jenkins -- build get <job> <n>` for the revision, then
   `github -- pr list --repo <owner/repo> --search "merged:<from>..<to>" --json number,title,mergedAt,url`.
3. Failures: `jenkins -- job list --recursive --status FAILURE -o json`, and `build log` for each failed deploy.
4. Health after each deploy, against the hour before it: error rate and latency for the
   deployed services (`cubeapm -- metrics query-range` with the environment label, or
   `grafana -- datasource query`), and `grafana -- annotation list --tags deploy`.

## Reading the numbers

- A successful CI run is not a deploy; confirm the deploy job ran and which revision it shipped.
- Compare each service to its own baseline; a nearby deploy does not prove a cause.
- Missing telemetry is a gap to report, not health.

## Output

A table of deploys (service, time, revision, result, health after), findings with
evidence, and follow-ups (failed deploys, regressions, rollbacks to consider). If a
regression looks like an incident, start `reckon debug` for that service.
