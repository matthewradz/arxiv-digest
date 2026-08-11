#!/bin/bash
# Learn from the papers you marked as wanted, and retune the digest.
#
#   ./learn.sh
#
# A thin wrapper over pipeline.py so every platform runs the same code.

set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || exit 1

# shellcheck source=_python.sh
. "$DIR/_python.sh"
PY="$(require_python)" || exit 127

exec "$PY" pipeline.py learn "$@"
