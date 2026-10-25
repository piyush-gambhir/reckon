# Node documentation tooling review

Reviewed against origin/main 0ee1991 on 2026-10-26 in an isolated worktree. No Rust, Python, application documentation content, CLI versions, or original checkout files were copied or changed. The original user's uncommitted work remains in the original checkout. This branch intentionally contains only the remote baseline and the Node documentation upgrade.

Node build tooling moves from 24.22.1 to 26.12.1, pnpm from 12.11.2 to 12.15.0. Matching Fumadocs core/UI move from 16.17.7 to 16.17.8, MDX from 15.4.9 to 15.4.10, lucide-react from 1.59.0 to 1.61.0, Wrangler from 4.151.3 to 4.152.0. Existing TypeScript 7.0.2 works directly without a compiler API compatibility package. Next remains security-pinned to 16.4.3. All existing security overrides and release-age exceptions remain. A range-bounded fast-uri 3.1.9 override fixes four development-only advisory entries through serve/ajv.

Validation uses exact Node 26.12.1 and pnpm 12.15.0:

- Frozen install, TypeScript/MDX/Next route checks, Cloudflare static build (114 generated pages), and search checks pass.
- Search test returns four installation results, checks the /reckon search endpoint, and checks absolute links in generated llms-full.txt.
- A real local workerd server serves the prepared assets. Disposable Chromium navigates Setup, uses the search dialog and selects its result, and fetches llms.txt, llms-full.txt, the Setup Markdown reference and its PNG image. No page errors or external network access.
- Wrangler preview deployment dry-run reads 530 local assets and exits without upload. Generated references come from the current remote main MDX content, not the original checkout's user-edited docs. No separate command-reference generator exists in the inspected scripts.
- Full and production audits report zero findings. Direct dependency outdated output is empty.

Build warnings remain in dependency tooling: webpack cannot statically follow MDX dynamic imports, and the CSS optimizer warns about ::highlight. They do not prevent export. No lint script or separate website unit-test suite is configured. No formatter dependency is installed; formatting was reviewed in the small configuration diff and git diff --check passes. Rust/Python behavior was outside this Node-only branch and was not retested.

Gains are current supported build tooling, matching documentation packages, and elimination of the development fast-uri audit findings while preserving newer security configuration. No site speed improvement was benchmarked. Cloudflare continues to serve static assets; Node 26 is not a replacement Workers runtime.

CI runs on main pushes and pull requests. No automatic website deployment workflow is configured. Production and preview deployments remain explicit Wrangler scripts; scripts/deploy-docs.sh also builds and deploys. None were invoked for upload. No release, provider API, credentials, or production resources were used.
