#!/usr/bin/env bash
# Build the Next.js site in web/ and deploy its static export as the Cloudflare
# Worker (static assets) that serves projects.piyushgambhir.com/reckon.
# Run as `bash scripts/deploy-docs.sh [production|development]` from an up-to-date main.
#
# `development` deploys the `preview` Wrangler environment to workers.dev instead.
# Credentials come from .env.deploy.<env> when present (for example
# CLOUDFLARE_API_TOKEN), otherwise from a local Wrangler login.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
mkdir -p "$ROOT_DIR/.wrangler"
export WRANGLER_LOG_PATH="${WRANGLER_LOG_PATH:-$ROOT_DIR/.wrangler/wrangler.log}"

ENV="${1:-production}"
case "$ENV" in
  production|development) ;;
  *)
    echo "error: expected production or development, got '$ENV'" >&2
    exit 1
    ;;
esac
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

# Only check the interactive login: whoami needs account-list access that a
# scoped deploy token may lack, and wrangler deploy reports token errors itself.
if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]] && ! ( cd "$WEB_DIR" && pnpm exec wrangler whoami >/dev/null 2>&1 ); then
  echo "error: Wrangler is not authenticated. Run 'cd web && pnpm exec wrangler login' or create $DEPLOY_ENV_FILE." >&2
  exit 1
fi

if [[ "$ENV" == "production" ]]; then
  echo "==> Deploying the Worker to projects.piyushgambhir.com/reckon"
  ( cd "$WEB_DIR" && pnpm deploy:cloudflare )
else
  echo "==> Deploying the preview Worker to workers.dev"
  ( cd "$WEB_DIR" && pnpm deploy:cloudflare:preview )
fi
