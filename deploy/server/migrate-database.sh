#!/usr/bin/env bash
# Explicit maintenance only. This script is never called by start-server.sh.
set -euo pipefail
operation=database-migration
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/server-common.sh"
target_schema="${1:-}"
[[ "${target_schema}" =~ ^[a-z][a-z0-9_]{0,62}$ ]] || die "Usage: bash migrate-database.sh TARGET_SCHEMA"
backup_dir="${deploy_dir}/database-backups/$(date -u +%Y%m%dT%H%M%SZ)-${target_schema}"
mkdir -p -- "${backup_dir}"
args=(--schema "${target_schema}" --apply --backup-dir /database-backup/export)
if [[ "${target_schema}" == public ]]; then args+=(--allow-public); fi
# The new image must already be loaded. The configured running service is not
# stopped or recreated. Only an existing v3 application schema is supported;
# owned cfg tables are rebuilt with backup, while protected forj tables stay in place.
docker compose --project-name forj-review --project-directory "${deploy_dir}" --env-file "${deploy_dir}/.env" \
  -f "${deploy_dir}/docker-compose.server.yml" run --rm --no-deps --user "$(id -u):$(id -g)" \
  --volume "${backup_dir}:/database-backup:rw" --entrypoint python "${service_name}" \
  -m forj_review_service.interfaces.database_tools migrate "${args[@]}"
echo "Migration completed. Backup: ${backup_dir}/export. Run start-server.sh to preflight and activate the new image."
