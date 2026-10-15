#!/usr/bin/env bash
set -euo pipefail
operation=stop
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/review-common.sh"
[[ -s "${compose_file}" ]] || die "Runtime Compose file is missing."
compose stop "${service_name}"
compose ps "${service_name}"
echo "Review Assistant stopped. Images, credentials, logs and rollback files retained."
