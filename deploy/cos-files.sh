#!/usr/bin/env bash
set -euo pipefail

action="${1:-}"
revision="${2:-}"
case "$action" in
  inventory|upload) ;;
  *) echo 'ERROR: expected inventory or upload' >&2; exit 1 ;;
esac
if [[ ! "$revision" =~ ^[a-f0-9]{40}$ ]] || [[ "$(git rev-parse HEAD)" != "$revision" ]]; then
  echo 'ERROR: COS task does not match the deployed revision' >&2
  exit 1
fi
container_id="$(sudo -n docker ps --filter label=com.docker.compose.project=intelligent-customer-service --filter label=com.docker.compose.service=api --format '{{.ID}}')"
if [[ ! "$container_id" =~ ^[a-f0-9]{12,64}$ ]]; then
  echo 'ERROR: expected exactly one running API container' >&2
  exit 1
fi
exec sudo -n docker exec -i "$container_id" python -m app.operations.cos_files "$action" --revision "$revision"
