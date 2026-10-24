#!/usr/bin/env bash
# Initialize a fresh application directory; preserve existing secrets.
set -euo pipefail
umask 0077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
for command in docker curl flock; do
  command -v "${command}" >/dev/null || { echo "Missing command: ${command}. See README.md." >&2; exit 1; }
done
docker info >/dev/null 2>&1 || { echo "Docker is unavailable. Start Docker and grant this user access; see README.md." >&2; exit 1; }
docker compose version >/dev/null
mkdir -p .runtime
for pair in 'review.env.example .env'; do
  read -r template target <<<"${pair}"
  if [[ ! -e "${target}" ]]; then
    install -m 600 "${template}" "${target}"
    echo "Created ${target}; fill in your credentials."
  else
    chmod 600 "${target}"
    echo "Preserved ${target}."
  fi
done
chmod u+x ./*.sh
echo "Application directory ready: $(pwd)"
echo "Audit rules and reference data are included in the image."
echo "Edit .env, then run ./start-server.sh."
