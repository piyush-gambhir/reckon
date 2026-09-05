# Reckon + CLI dependency and compatibility audit

Checked 2026-09-06 against public Go/npm registries, upstream release tags, and official API documentation.

## Scope

Six custom Go CLIs (CubeAPM, Elasticsearch, Grafana, Jenkins, Jira, Nginx Proxy Manager), their six documentation sites, and Reckon's setup scripts and documentation site. OpenSRE and globally installed third-party tools are outside this pass. Existing local Reckon edits were preserved. Task changes are committed and pushed in all seven repositories. All six CLI releases and all seven documentation sites are published. Unrelated pre-existing local edits remain uncommitted.

## Dependency baseline

| Component | Updated baseline |
| --- | --- |
| Go source / selected toolchain | Go 1.26 minimum / Go 1.27.1 |
| Cobra / pflag | 1.10.2 / 1.0.10 |
| flock / renameio v2 | 0.13.1 / 2.0.2 |
| YAML | maintained `go.yaml.in/yaml/v3` 3.0.5 |
| x/sys / x/term | 0.47.0 / 0.45.0 |
| Jenkins and Jira tablewriter | 1.1.4, migrated from the 0.0.5 API |
| Next.js / React | 16.3.4 / 19.2.8 |
| Fumadocs core and Base UI / MDX | 16.15.7 / 15.4.0 |
| TypeScript / Tailwind / Zod | 7.0.2 / 4.3.3 / 4.5.4 |
| Wrangler / pnpm | 4.129.0 / 11.25.0 |
| govulncheck / Staticcheck | 1.7.0 / 0.8.1 |
| GoReleaser / Syft | 2.18.0 / 1.51.1 |

Other direct docs dependencies were refreshed to the registry's latest stable releases. The six sites have matching dependency specifications; Reckon retains its exact font pins at the same current versions. All manifests and lockfiles were updated together. GitHub Actions are pinned to verified release commit hashes; dependency update PRs are configured weekly.

## Fixes beyond version bumps

- **Correct executable installation:** all six `make install` targets now build the public binary name. Their main package directory is named `cli-go`, so the previous `go install .` targets could overwrite a shared `cli-go` executable. `INSTALL_DIR` and `GOBIN` are honored.
- **Working Reckon release pins:** Bash and PowerShell share [cli-releases.csv](../scripts/cli-releases.csv). It maps the latest public releases (Grafana/Jenkins/CubeAPM 0.2.6; Elasticsearch 0.1.6) to matching nested-module semantic version tags. Private staging directories prevent binary collisions. Setup remains idempotent and leaves already-installed commands in place.
- **CubeAPM request contract:** trace search sends `query`; trace search/fetch send Unix seconds as documented. Promoting the environment filter no longer mutates the caller's tag map. Jaeger-shaped output still uses its existing internal time representation. Contract tests cover the request units and repeated use of filters.
- **Table output:** Jenkins/Jira use the current tablewriter API, preserve borderless output, and propagate writer errors. Tests cover long values, Unicode content, empty slices, pointers to slices, JSON fallback, and broken output writers.
- **Maintained YAML:** all six CLIs use the maintained YAML organization v3 package, with existing config/output tests retained.
- **Fumadocs search migration:** replaced the obsolete Orama initializer with the current default static search client and removed the direct Orama dependency. A built-index test exercises the installed search client against each site's actual exported index.
- **Docs and CI:** corrected stale Go/source-install instructions and development-version examples; added compatibility pages, top-level command documentation checks, docs builds/audits, search-index tests, and named-executable installation checks to CI.

## Validation

| Check | Result |
| --- | --- |
| All six CLIs: `go mod verify`, `go mod tidy -diff` | Passed |
| All six CLIs: `go vet`, full `go test -race -count=1` | Passed |
| All six CLIs: govulncheck and Staticcheck | Passed; no reported reachable vulnerabilities |
| All six CLIs: existing top-level `check-llms.sh` | Passed |
| All six `make install` targets, temporary destinations, `version` smoke tests | Passed |
| Reckon: actual installation of all four pinned public releases into a temporary directory | Passed; expected version output |
| Reckon: 10 existing control-surface tests | Passed |
| Reckon: new mocked installer tests | Passed: naming, GOBIN preservation, spaces in paths, failure cleanup, idempotency |
| All seven docs sites: TypeScript checks and production static builds | Passed |
| All seven docs sites: exported static search-index checks | Passed |
| All seven docs sites: frozen-lockfile installs | Passed |
| All seven docs sites: production dependency audit | No known vulnerabilities reported |
| Workflow YAML parsing and `git diff --check` | Passed |

Go/application tests use local fixtures and mock HTTP servers. Production infrastructure was not queried. PowerShell passed the GitHub CI parser gate; native Windows execution was not available locally. All seven GitHub CI pipelines and all six release workflows passed. Release archives, checksums, SBOMs, and build provenance attestations were published. All four newly tagged Reckon installs were verified in an isolated temporary directory.

## API references and compatibility limits

| CLI | Reference and remaining boundary |
| --- | --- |
| CubeAPM | [Official trace API](https://docs.cubeapm.com/http-apis/traces-apis); rolling docs, not a versioned manual. Native request parameters are now the default; optional Jaeger-derived operations and extra filters remain deployment-dependent. |
| Elasticsearch | [REST API v9](https://www.elastic.co/docs/api/doc/elasticsearch/v9/), upstream 9.5.3; features and SQL still depend on server version/license. |
| Grafana | [Grafana 13.2 HTTP API](https://grafana.com/docs/grafana/v13.2/developer-resources/api-reference/http-api/), upstream 13.2.1. The CLI still uses deprecated `/api` routes. Full adaptation to versioned `/apis` resources remains separate work; it is not claimed complete. |
| Jenkins | [Remote Access API](https://www.jenkins.io/doc/book/using/remote-access-api/) and [CSRF rules](https://www.jenkins.io/doc/book/security/csrf-protection/); controller/plugin-specific contracts have no single REST API version. |
| Jira | [Cloud REST v3 enhanced search](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/); existing Cloud cursor pagination and explicit fields are current. Data Center retains its separate classic v2 search. |
| Nginx Proxy Manager | [2.15.1 release-tagged schemas](https://github.com/NginxProxyManager/nginx-proxy-manager/tree/v2.15.1/backend/schema); no independently versioned stable public API contract. |

Dependency freshness and mock contract checks are not an exhaustive certification against every deployed server. Grafana's full `/apis` migration and live-server acceptance checks are the principal remaining compatibility work. Go 1.27-built macOS binaries require macOS 13 or later ([release notes](https://go.dev/doc/go1.27)).

## Recheck

In each `cli-go/` directory, run `go list -m -u all` for new modules and the checks above before accepting upgrades. In each `web/`, run `pnpm outdated`, `pnpm audit --prod`, `pnpm types:check`, `pnpm build:cloudflare`, and `pnpm test:search`. When updating Reckon's release CSV, verify each nested-module version tag matches the corresponding public release tag and smoke-test the named executable in a temporary directory.

## Published releases

| CLI | Release | Documentation |
| --- | --- | --- |
| cubeapm-cli | [0.2.6](https://github.com/piyush-gambhir/cubeapm-cli/releases/tag/v0.2.6) | [Live docs](https://projects.piyushgambhir.com/cubeapm-cli/docs/compatibility) |
| grafana-cli | [0.2.6](https://github.com/piyush-gambhir/grafana-cli/releases/tag/v0.2.6) | [Live docs](https://projects.piyushgambhir.com/grafana-cli/docs/compatibility) |
| jenkins-cli | [0.2.6](https://github.com/piyush-gambhir/jenkins-cli/releases/tag/v0.2.6) | [Live docs](https://projects.piyushgambhir.com/jenkins-cli/docs/compatibility) |
| es-cli | [0.1.6](https://github.com/piyush-gambhir/es-cli/releases/tag/v0.1.6) | [Live docs](https://projects.piyushgambhir.com/es-cli/docs/compatibility) |
| jira-cli | [0.1.6](https://github.com/piyush-gambhir/jira-cli/releases/tag/v0.1.6) | [Live docs](https://projects.piyushgambhir.com/jira-cli/docs/compatibility) |
| nginxpm-cli | [0.1.6](https://github.com/piyush-gambhir/nginxpm-cli/releases/tag/v0.1.6) | [Live docs](https://projects.piyushgambhir.com/nginxpm-cli/docs/compatibility) |

[Reckon installation documentation](https://projects.piyushgambhir.com/reckon/docs/installation) includes the shared release pins. Public documentation and search endpoints returned HTTP 200; all seven search exports matched the local builds.
