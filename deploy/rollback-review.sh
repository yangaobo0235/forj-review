#!/usr/bin/env bash
set -euo pipefail
operation=rollback
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/review-common.sh"
docker image inspect "${rollback_image}" >/dev/null 2>&1 || die "No rollback image is available."
[[ -s "${runtime_dir}/rollback-compose.yml" ]] || die "No matching rollback Compose file is available."
docker compose --project-directory "${runtime_dir}" --env-file "${runtime_dir}/.env" \
  -f "${runtime_dir}/rollback-compose.yml" config --quiet
docker tag "${rollback_image}" "${image_name}"
prepare_logs
cp "${runtime_dir}/rollback-compose.yml" "${compose_file}"
compose up -d --no-build --force-recreate "${service_name}"
wait_healthy
