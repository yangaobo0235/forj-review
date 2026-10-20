#!/usr/bin/env bash
set -euo pipefail
operation=stop
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/server-common.sh"
[[ -s "${compose_file}" ]] || die "Runtime Compose file is missing."
compose stop "${service_name}"
compose ps "${service_name}"
echo "FORJ Review Service stopped. Images, credentials, logs and rollback files retained."
