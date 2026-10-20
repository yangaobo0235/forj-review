#!/usr/bin/env bash
# Initialize a fresh application directory; preserve existing secrets and database.
set -euo pipefail
umask 0077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
for command in docker curl flock; do
  command -v "${command}" >/dev/null || { echo "Missing command: ${command}. See README.md." >&2; exit 1; }
done
docker info >/dev/null 2>&1 || { echo "Docker is unavailable. Start Docker and grant this user access; see README.md." >&2; exit 1; }
docker compose version >/dev/null
mkdir -p .runtime
for pair in 'review.env.example .env' 'database.env.example .env.database'; do
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
docker network inspect postgres_network >/dev/null 2>&1 || docker network create postgres_network
echo "Application directory ready: $(pwd)"
echo "Use the existing PostgreSQL database; no database was installed or reset."
echo "Edit .env and .env.database, verify postgres is attached to postgres_network, then run ./start-server.sh."
