#!/usr/bin/env bash
# Build the Next.js site in web/ and deploy its static export as the Cloudflare
# Worker (static assets) that serves projects.piyushgambhir.com/reckon.
# Run as `bash scripts/deploy-docs.sh` from an up-to-date main.
#
# Credentials come from .env.deploy.production when present (for example
# CLOUDFLARE_API_TOKEN), otherwise from a local Wrangler login.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

ENV="${1:-production}"
if [[ "$ENV" != "production" ]]; then
  echo "error: only 'production' is supported; the site has no preview environment." >&2
  exit 1
fi
DEPLOY_ENV_FILE=".env.deploy.${ENV}"

if [[ -f "$DEPLOY_ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$DEPLOY_ENV_FILE"
  set +a
fi

WEB_DIR="${WEB_DIR:-web}"

echo "==> Building the site in ${WEB_DIR}/"
( cd "$WEB_DIR" && pnpm install --frozen-lockfile && pnpm build:cloudflare && pnpm test:search )

if ! ( cd "$WEB_DIR" && pnpm exec wrangler whoami >/dev/null 2>&1 ); then
  echo "error: Wrangler is not authenticated. Run 'cd web && pnpm exec wrangler login' or create $DEPLOY_ENV_FILE." >&2
  exit 1
fi

echo "==> Deploying the Worker to projects.piyushgambhir.com/reckon"
( cd "$WEB_DIR" && pnpm deploy:cloudflare )
