#!/usr/bin/env bash
set -euo pipefail
operation=start
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/review-common.sh"
image_tar="${upload_dir}/forj-review-api-main.tar"
uploaded_compose="${upload_dir}/docker-compose.server.yml"
[[ -s "${image_tar}" ]] || die "Upload forj-review-api-main.tar before starting."
[[ -s "${uploaded_compose}" ]] || die "Upload docker-compose.server.yml before starting."
if [[ -f "${upload_dir}/SHA256SUMS" ]]; then
  (cd "${upload_dir}" && sha256sum --check --status SHA256SUMS) || die "Release checksum verification failed; upload the complete release again."
else
  die "Missing SHA256SUMS. Upload ALL files from .tmp/server-release, including updated scripts."
fi
# Check configuration before replacing the runtime file or recreating containers.
docker compose --project-directory "${runtime_dir}" --env-file "${runtime_dir}/.env" \
  -f "${uploaded_compose}" config --quiet
previous_image=""
previous_compose="${compose_file}"
if [[ -s "${runtime_dir}/.last-successful-image" ]]; then
  previous_image="$(cat "${runtime_dir}/.last-successful-image")"
  previous_compose="${runtime_dir}/.last-successful-compose.yml"
else
  previous_image="$(docker inspect --format '{{.Image}}' review-agent-service 2>/dev/null || true)"
  if [[ -z "${previous_image}" ]]; then
    previous_image="$(docker image inspect --format '{{.Id}}' "${image_name}" 2>/dev/null || true)"
  fi
  if [[ -z "${previous_image}" ]]; then
    previous_image="$(docker image inspect --format '{{.Id}}' review-agent-service:main 2>/dev/null || true)"
  fi
fi
if [[ -n "${previous_image}" && ! -s "${previous_compose}" ]]; then
  die "Previous image exists but its Compose file is missing; restore ${previous_compose} before upgrading so rollback stays complete."
fi
echo "Loading release image..."
docker load --input "${image_tar}"
new_image="$(docker image inspect --format '{{.Id}}' "${image_name}")"
[[ -n "${new_image}" ]] || die "Release did not provide ${image_name}."
# Re-running an identical release must not destroy the genuine previous version.
if [[ -n "${previous_image}" ]] && { [[ "${previous_image}" != "${new_image}" ]] || ! cmp -s "${uploaded_compose}" "${previous_compose}"; }; then
  docker tag "${previous_image}" "${rollback_image}"
  if [[ -s "${previous_compose}" ]]; then cp "${previous_compose}" "${runtime_dir}/rollback-compose.yml"; fi
  if [[ ! -s "${runtime_dir}/.last-successful-image" ]]; then
    printf '%s\n' "${previous_image}" >"${runtime_dir}/.last-successful-image"
    if [[ -s "${previous_compose}" ]]; then cp "${previous_compose}" "${runtime_dir}/.last-successful-compose.yml"; fi
  fi
  echo "Previous image and Compose configuration retained for rollback."
fi
prepare_logs
cp "${uploaded_compose}" "${compose_file}"
compose up -d --no-build --force-recreate "${service_name}"
wait_healthy
