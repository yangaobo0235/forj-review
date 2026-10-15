#!/usr/bin/env bash
# Shared by the three release entrypoints; no secrets are printed.
set -euo pipefail
umask 0027
upload_dir="${REVIEW_UPLOAD_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}"
runtime_dir="${REVIEW_RUNTIME_DIR:-/opt/review-assistant}"
compose_file="${runtime_dir}/docker-compose.server.yml"
image_name="forj-review-api:main"
rollback_image="forj-review-api:rollback"
service_name="review-agent"
health_url="${REVIEW_HEALTH_URL:-http://127.0.0.1:18110/health}"
export REVIEW_UPLOAD_DIR="${upload_dir}"

die() { echo "ERROR: $*" >&2; exit 1; }
for command in docker curl flock; do command -v "${command}" >/dev/null || die "Missing command: ${command}"; done
docker info >/dev/null 2>&1 || die "Docker is not available."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required."
[[ -d "${runtime_dir}" && -w "${runtime_dir}" ]] || die "Create ${runtime_dir} and give the deployment user write access first."
[[ -s "${runtime_dir}/.env" ]] || die "Missing ${runtime_dir}/.env; create it on the server, never upload secrets."
exec 9>"${runtime_dir}/.deploy.lock"
flock -n 9 || die "Another deployment operation is running."
# Small operator log, separate from application logs and bounded to two files.
if [[ -f "${upload_dir}/deploy.log" ]] && [[ $(wc -c <"${upload_dir}/deploy.log") -gt 5242880 ]]; then
  mv -f "${upload_dir}/deploy.log" "${upload_dir}/deploy.log.1"
fi
exec > >(tee -a "${upload_dir}/deploy.log" 9>&-) 2>&1
echo "$(date -Iseconds) deployment operation=${operation}"

compose() { docker compose --project-directory "${runtime_dir}" --env-file "${runtime_dir}/.env" -f "${compose_file}" "$@"; }

prepare_logs() {
  local log_dir="${upload_dir}/logs"
  mkdir -p "${log_dir}"
  # The image knows its application UID (also supports pre-migration images).
  # A setgid directory lets the host operator read files without world access.
  docker run --rm --network none --read-only --user 0:0 --entrypoint python \
    --mount "type=bind,src=${log_dir},dst=/logs" "${image_name}" -c '
import os, pathlib, pwd, sys
uid, gid = pwd.getpwnam("review-agent").pw_uid, int(sys.argv[1])
root = pathlib.Path("/logs")
os.chown(root, uid, gid); os.chmod(root, 0o2770)
for path in root.glob("review-agent.log*"):
    if path.is_file() and not path.is_symlink():
        os.chown(path, uid, gid); os.chmod(path, 0o640)
' "$(id -g)"
}

wait_healthy() {
  for attempt in {1..20}; do
    if curl --fail --silent --connect-timeout 2 --max-time 5 "${health_url}" >/dev/null; then
      docker image inspect --format '{{.Id}}' "${image_name}" >"${runtime_dir}/.last-successful-image"
      cp "${compose_file}" "${runtime_dir}/.last-successful-compose.yml"
      echo "Review Assistant started successfully. Health: ${health_url}"
      compose ps "${service_name}"
      return 0
    fi
    sleep 3
  done
  echo "ERROR: health check failed. Previous release remains available via ./rollback-review.sh" >&2
  compose logs --tail=100 "${service_name}" >&2
  return 1
}
