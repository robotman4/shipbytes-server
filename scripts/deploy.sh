#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${DEPLOY_HOST:=node2}"
: "${DEPLOY_PATH:=}"
: "${DEPLOY_USER:=}"
: "${SITE_URL:?Export SITE_URL for the final HTTPS health check}"
target="${DEPLOY_USER:+$DEPLOY_USER@}$DEPLOY_HOST"
if [ -z "$DEPLOY_PATH" ]; then DEPLOY_PATH=$(ssh "$target" 'printf "%s/docker/shipbytes" "$HOME"'); fi
[[ "$DEPLOY_HOST" =~ ^[a-zA-Z0-9._-]+$ && "$DEPLOY_PATH" =~ ^/[a-zA-Z0-9/_-]+$ && "$DEPLOY_USER" =~ ^[a-zA-Z0-9_-]*$ ]] || { echo 'Invalid deployment configuration' >&2; exit 1; }
[[ "$SITE_URL" == https://* ]] || { echo 'SITE_URL must use HTTPS' >&2; exit 1; }
target="${DEPLOY_USER:+$DEPLOY_USER@}$DEPLOY_HOST"
make test
arch=$(ssh "$target" uname -m)
case "$arch" in x86_64) platform=linux/amd64;; aarch64|arm64) platform=linux/arm64;; *) echo "Unsupported architecture: $arch" >&2; exit 1;; esac
image="shipbytes:$(date -u +%Y%m%dT%H%M%SZ)"
docker buildx build --platform "$platform" --target production --load -t "$image" .
ssh "$target" "test -f '$DEPLOY_PATH/.env'" || { echo "Create private $DEPLOY_PATH/.env on host first" >&2; exit 1; }
# pipefail on both sides detects failures in save, compression, SSH and load.
docker save "$image" | gzip | ssh "$target" 'bash -o pipefail -c "gunzip | docker load"'
scp compose.yml Caddyfile "$target:$DEPLOY_PATH/"
ssh "$target" "mkdir -p '$DEPLOY_PATH/scripts'"
scp scripts/backup.sh scripts/sync-and-publish.sh "$target:$DEPLOY_PATH/scripts/"
ssh "$target" bash -s -- "$DEPLOY_PATH" "$image" <<'REMOTE'
set -euo pipefail
cd "$1"
if [ -f .release.env ]; then cp .release.env .previous-release.env; fi
printf 'SHIPBYTES_IMAGE=%s\n' "$2" > .candidate-release.env
compose() { docker compose --env-file .env --env-file .candidate-release.env "$@"; }
if docker inspect shipbytes-app >/dev/null 2>&1; then bash scripts/backup.sh; fi
compose run --rm -T --interactive=false app python -m alembic upgrade head
compose up -d --wait --wait-timeout 120
mv .candidate-release.env .release.env
REMOTE
curl --fail --silent --show-error --retry 12 --retry-delay 5 --retry-all-errors --max-time 10 "${SITE_URL%/}/health" | python3 -c 'import json,sys; assert json.load(sys.stdin)=={"status":"ok"}'
echo "Deployed $image to $SITE_URL"
