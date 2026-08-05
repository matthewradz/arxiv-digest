#!/bin/bash
# ---------------------------------------------------------------------------
#  Learn from the papers you actually downloaded.
#
#  Reads the ticked boxes in library.md, compares them against everything the
#  digest surfaced, and rewrites preferences.md — which the nightly digest reads
#  to decide what to show you.
#
#    ./learn.sh
# ---------------------------------------------------------------------------

set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || exit 1

# shellcheck source=_engine.sh
. "$DIR/_engine.sh"
detect_engine || exit 127

# shellcheck source=_python.sh
. "$DIR/_python.sh"
PY="$(require_python)" || exit 127

echo "Reading ticked papers from library.md..."
"$PY" pick.py --sync || exit 1
echo

REPORT="runs/learn-report.md"
"$PY" learn.py >"$REPORT" || exit 1

if grep -q '^NO_PICKS' "$REPORT"; then
  echo "No picks recorded yet — nothing to learn from."
  echo
  echo "Open library.md, change '- [ ]' to '- [x]' on the papers you downloaded,"
  echo "then run ./learn.sh again."
  exit 0
fi

NPICKS="$(grep -c '^- \[' "$REPORT" 2>/dev/null || echo 0)"
echo "Analysing your picks..."

TMP="runs/learn-output.md"
INPUT="runs/learn-input.md"
{
  cat "$REPORT"
  echo
  echo "## Current preferences.md"
  echo
  if [[ -f preferences.md ]]; then cat preferences.md; else echo "(none yet)"; fi
} >"$INPUT"

if ! engine_run "$DIR/learn_prompt.md" "$INPUT" "$TMP" runs/learn-error.log \
   || [[ ! -s "$TMP" ]]; then
  echo "$(engine_name) produced no output; see runs/learn-error.log." >&2
  exit 1
fi

# Split on the marker: preferences above, config suggestions below.
if grep -q '^===CONFIG-SUGGESTIONS===' "$TMP"; then
  awk '/^===CONFIG-SUGGESTIONS===/{f=1; next} !f' "$TMP" >preferences.md
  awk '/^===CONFIG-SUGGESTIONS===/{f=1; next}  f' "$TMP" >config-suggestions.md
else
  cp "$TMP" preferences.md
  : >config-suggestions.md
fi

echo
echo "Updated preferences.md — tomorrow's digest will use it."
echo
echo "=================== suggested config.toml edits ==================="
if [[ -s config-suggestions.md ]]; then cat config-suggestions.md; else
  echo "(none)"; fi
echo "==================================================================="
echo
echo "These are suggestions only; nothing was changed in config.toml."
echo "Full statistics: $REPORT"
