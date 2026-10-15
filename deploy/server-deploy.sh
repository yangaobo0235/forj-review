#!/usr/bin/env bash
# Legacy entrypoint. Use the same checked release workflow as start-review.sh.
set -euo pipefail
exec bash "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/start-review.sh" "$@"
