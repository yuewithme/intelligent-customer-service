#!/usr/bin/env bash
set -Eeuo pipefail

repository="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project_name="intelligent-customer-service"
compose_file="$repository/docker-compose.prod.yml"

export BACKEND_ENV_FILE="${BACKEND_ENV_FILE:-/etc/intelligent-customer-service/backend.env}"
export APP_DATA_DIR="${APP_DATA_DIR:-/srv/intelligent-customer-service/data}"
export HF_CACHE_DIR="${HF_CACHE_DIR:-/srv/intelligent-customer-service/cache/huggingface}"
deploy_log_dir="${DEPLOY_LOG_DIR:-/srv/intelligent-customer-service/logs}"
lock_file="${DEPLOY_LOCK_FILE:-/tmp/intelligent-customer-service-deploy.lock}"
force_deploy="${1:-}"

mkdir -p "$deploy_log_dir"
log_file="$deploy_log_dir/auto-deploy.log"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$log_file"
}

compose() {
  sudo env \
    BACKEND_ENV_FILE="$BACKEND_ENV_FILE" \
    APP_DATA_DIR="$APP_DATA_DIR" \
    HF_CACHE_DIR="$HF_CACHE_DIR" \
    docker compose -p "$project_name" -f "$compose_file" "$@"
}

exec 9>"$lock_file"
if ! flock -n 9; then
  exit 0
fi

if [[ ! -f "$BACKEND_ENV_FILE" ]]; then
  log "ERROR: production environment file is missing: $BACKEND_ENV_FILE"
  exit 1
fi
if [[ ! -d "$APP_DATA_DIR" || ! -d "$HF_CACHE_DIR" ]]; then
  log "ERROR: production data directories are not prepared"
  exit 1
fi

cd "$repository"
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  log "ERROR: deployment checkout contains tracked changes"
  exit 1
fi

git fetch origin main --quiet
current_revision="$(git rev-parse HEAD)"
target_revision="$(git rev-parse origin/main)"
if [[ "$force_deploy" != "--force" && "$current_revision" == "$target_revision" ]]; then
  exit 0
fi

git merge --ff-only origin/main --quiet
target_revision="$(git rev-parse HEAD)"
log "Deploying revision $target_revision"

compose config --quiet
compose up -d --build

for attempt in $(seq 1 24); do
  if curl -fsS --max-time 5 http://127.0.0.1/health >/dev/null; then
    log "Deployment healthy at revision $target_revision"
    exit 0
  fi
  sleep 5
done

log "ERROR: deployment health check failed at revision $target_revision"
compose ps | tee -a "$log_file"
exit 1
