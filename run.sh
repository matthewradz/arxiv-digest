#!/bin/bash
# Nightly arXiv digest.
#
#   ./run.sh              build tonight's digest (skips if already built)
#   ./run.sh --force      rebuild even if tonight's digest exists
#   ./run.sh --open       build, then open the digest
#
# A thin wrapper: the pipeline itself is pipeline.py, so macOS, Windows and
# Linux all run the same code.

set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || exit 1

# shellcheck source=_python.sh
. "$DIR/_python.sh"
PY="$(require_python)" || exit 127

exec "$PY" pipeline.py digest "$@"
