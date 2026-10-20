#!/usr/bin/env bash
# Shared by the three release entrypoints; no secrets are printed.
set -euo pipefail
umask 0027
deploy_dir="${REVIEW_DEPLOY_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}"
runtime_dir="${REVIEW_RUNTIME_DIR:-${deploy_dir}/.runtime}"
compose_file="${runtime_dir}/docker-compose.server.yml"
image_name="forj-review-service:main"
rollback_image="forj-review-service:rollback"
service_name="review-service"
health_url="${REVIEW_HEALTH_URL:-http://127.0.0.1:26101/health}"
export REVIEW_DEPLOY_DIR="${deploy_dir}"

die() { echo "ERROR: $*" >&2; exit 1; }
for command in docker curl flock; do command -v "${command}" >/dev/null || die "Missing command: ${command}"; done
docker info >/dev/null 2>&1 || die "Docker is not available."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required."
[[ -d "${runtime_dir}" && -w "${runtime_dir}" ]] || die "Create ${runtime_dir} and give the deployment user write access first."
[[ -s "${deploy_dir}/.env" && -s "${deploy_dir}/.env.database" ]] || die "Run bash setup-server.sh, then fill in .env and .env.database on the server."
exec 9>"${runtime_dir}/.deploy.lock"
flock -n 9 || die "Another deployment operation is running."
# Small operator log, separate from application logs and bounded to two files.
if [[ -f "${deploy_dir}/deploy.log" ]] && [[ $(wc -c <"${deploy_dir}/deploy.log") -gt 5242880 ]]; then
  mv -f "${deploy_dir}/deploy.log" "${deploy_dir}/deploy.log.1"
fi
exec > >(tee -a "${deploy_dir}/deploy.log" 9>&-) 2>&1
echo "$(date -Iseconds) deployment operation=${operation}"

compose() { docker compose --project-name forj-review --project-directory "${deploy_dir}" --env-file "${deploy_dir}/.env" -f "${compose_file}" "$@"; }

prepare_logs() {
  local log_dir="${deploy_dir}/logs"
  mkdir -p "${log_dir}"
  # The image defines the application UID.
  # A setgid directory lets the host operator read files without world access.
  docker run --rm --network none --read-only --user 0:0 --entrypoint python \
    --mount "type=bind,src=${log_dir},dst=/logs" "${image_name}" -c '
import os, pathlib, pwd, sys
uid, gid = pwd.getpwnam("review-service").pw_uid, int(sys.argv[1])
root = pathlib.Path("/logs")
os.chown(root, uid, gid); os.chmod(root, 0o2770)
for path in root.glob("review-service.log*"):
    if path.is_file() and not path.is_symlink():
        os.chown(path, uid, gid); os.chmod(path, 0o640)
' "$(id -g)"
}

wait_healthy() {
  for attempt in {1..20}; do
    if curl --fail --silent --connect-timeout 2 --max-time 5 "${health_url}" >/dev/null; then
      docker image inspect --format '{{.Id}}' "${image_name}" >"${runtime_dir}/.last-successful-image"
      cp "${compose_file}" "${runtime_dir}/.last-successful-compose.yml"
      echo "FORJ Review Service started successfully. Health: ${health_url}"
      compose ps "${service_name}"
      return 0
    fi
    sleep 3
  done
  echo "ERROR: health check failed. Previous release remains available via ./rollback-server.sh" >&2
  compose logs --tail=100 "${service_name}" >&2
  return 1
}
