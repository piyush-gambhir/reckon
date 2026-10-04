# Workspace readiness and isolation review

Reviewed 2026-09-08: local source, configuration boundaries, collection, storage and
remaining product work. No infrastructure queries, database access, publishing,
deployments or external messages were performed.

## Separate development checkout

The existing `reckon/` checkout was on `main` at `53cba0b` with 48 modified/untracked
source files, including earlier unfinished work. Its source changes were preserved.

A sibling `reckon-dev/` worktree on `codex/workspace-readiness` contains a
byte-verified snapshot of all 163 tracked/nonignored source files. Initial snapshot
commit `e706a77` is a WIP preservation checkpoint, not a release approval. Review
fixes and planning changes follow separately.

No environment credentials, `.config/`, `.reckon-env`, `infra-knowledge/`,
`incidents/`, `sessions/`, demo output, installed binaries or web dependencies were
copied. Development selects no infrastructure environment by default. Synthetic
runs create their own ignored state. Worktrees share Git objects/refs but have
separate checked-out files and tenant data directories.

Continue source work in `reckon-dev/`; keep real credentials and investigations in
the operator checkout. Do not copy ignored operator directories into development.
The original checkout intentionally retains its uncommitted work; do not reset or
clean it to make its status look tidy.

The preservation commit is local and unsigned because the configured signing agent
was unavailable. No signing configuration was changed and nothing was pushed. Use
the normal signed review/release process when publishing.

## Findings fixed

| Finding | Previous behavior | Fix and regression evidence |
| --- | --- | --- |
| Evidence directory redirection | Symlinked `evidence/` could redirect collected output outside a session | Validate path components below state roots; external fixture directory stays untouched |
| Metadata/lock links | `session.json` and `.lock` followed links; locking could change external permissions | Reject links/junctions, use no-follow opens where available, require a private regular lock file |
| Unchecked artifact integrity | Edited artifacts could be reused, cited, reported and promoted | Verify paths, bytes and SHA-256; reject missing/linked/changed artifacts and successful records without artifacts |
| Promotion copied extra links | Copying could dereference unrelated files into an incident | Validate entries, copy without following links, validate the copied snapshot/evidence again |
| Report consistency | Content was read before the write lock; promotion could copy an older report | Hold the session lock across reading, report writing and promotion |
| Global default paths | Children inherited operator HOME/cache/temp despite XDG scoping | Private per-provider/per-environment defaults; fixture proves a global credential sentinel is not found through HOME |
| Provider configuration scope | All providers received `KUBECONFIG` | Supply it only to Kubernetes |
| Elasticsearch guard | `.env` could override the read-only default with `false` | Force the activation flag, matching the other guarded CLIs |
| State permissions/bytes | Intermediate mkdir permissions followed umask; Windows text writes could translate bytes | Explicit private creation for missing parents and canonical LF artifact writes |

Regressions run before the fixes reproduced the link, integrity, provider-variable
and Elasticsearch failures using synthetic data. The full offline suite now passes.

## Validated boundaries

- Collection refuses a session/environment mismatch before a provider read.
  Credential loading uses a fresh child environment.
- Typed operations use validated argument arrays, with no arbitrary shell or
  database operation. Execution has per-command time/output limits and POSIX cleanup.
- State uses private files/directories, atomic replacements and writer locks.
  Evidence references are checked against session/environment and artifacts.
- Credentials, profiles, knowledge, sessions and incidents remain ignored. Examples
  and test fixtures are synthetic. Local planning/reporting/demo need no provider access.

## Remaining limitations

| Limitation | Consequence and next work |
| --- | --- |
| Same-user filesystem access | Path checks are not an OS sandbox and do not defeat adversarial concurrent replacement. Hashes do not authenticate evidence against someone who can also edit metadata. Hosted isolation requires a separate enforced design. |
| Trusted configuration | `.env` executes shell; profiles can reference external files/helpers and Kubernetes credential plugins. Treat configuration as trusted code and test real adapter contracts. |
| Probe/collector differences | Collector HOME/cache/temp are private; direct activation/verify retain operator HOME for agent logins. Align contracts and explicitly support SSO flows; never silently fall back to global tokens. |
| Incomplete redaction | Arbitrary saved-profile secrets, notes/questions and customer log data are not comprehensively scrubbed. Add profile-aware redaction and reviewed export; local reports are not public artifacts. |
| Generic output handling | Some partial/page-limited payloads retain `status=ok`; broad JSON acceptance and automatic reuse need source-specific contracts. |
| Incomplete provenance | Fingerprints omit endpoint/profile identity and binary version. Record non-secret source identity and define refresh on configuration change. |
| Platform coverage | No native Windows acceptance in this review. Native activation, process cleanup and storage need CI before a support claim. |
| No measured diagnostic benefit | The demo proves plumbing. Eight replay scenarios and coding-agent comparisons must establish correctness and speed. |

No local guard establishes server-side read-only permissions. Existing role
requirements and per-query database approvals continue to apply.

## Verification

Run from the development checkout:

```bash
python3 -B -m unittest discover -s tests -p 'test_*.py' -v
bash tests/test-reckon.sh
bash tests/test-cli-install.sh
bash -n .envrc scripts/reckon
git diff --check
./scripts/reckon demo
```

This review: 42 Python tests pass, 12 shell tests pass, and installer fixtures pass
on macOS with Python 3.9.6. Tests use temporary configuration and fake provider
executables. The separate offline demo completed with seven evidence records and
an evidence-linked report. Live-server compatibility and native Windows remain
unverified. All 163 original source hashes and the original dirty status were
checked again after the fixes and remained unchanged.

The previous implementation pass validated website types, a 90-page static build
and search. This review changes Python, shell activation and Markdown planning/
storage documentation; web dependencies/output were not copied or rebuilt here.

See [PRODUCT-PLAN.md](PRODUCT-PLAN.md) for ordered slices and acceptance gates.
