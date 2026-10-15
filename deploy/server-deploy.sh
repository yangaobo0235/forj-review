#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${project_dir}/.env"

cd "${project_dir}"

if [[ ! -s "${env_file}" ]]; then
  echo "Missing ${env_file}. Create it and configure DASHSCOPE_API_KEY first." >&2
  exit 1
fi

if ! docker image inspect forj-review-api:main >/dev/null 2>&1; then
  echo "Missing forj-review-api:main. Load the locally built image before deploying." >&2
  exit 1
fi

docker compose -f docker-compose.server.yml up -d --no-build --force-recreate review-agent
docker compose -f docker-compose.server.yml ps

echo
echo "Waiting for the local health endpoint..."
for attempt in {1..20}; do
  if curl --fail --silent --show-error http://127.0.0.1:18110/health; then
    echo
    echo "Review Agent is healthy on server-local port 18110."
    exit 0
  fi
  sleep 3
done

echo "Health check did not become ready. Inspect logs with:" >&2
echo "docker compose -f docker-compose.server.yml logs --tail=200 review-agent" >&2
exit 1
