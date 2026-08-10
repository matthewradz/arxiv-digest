#!/bin/bash
# ---------------------------------------------------------------------------
#  Nightly arXiv digest.
#
#    ./run.sh              build tonight's digest (skips if already built)
#    ./run.sh --force      rebuild even if tonight's digest exists
#    ./run.sh --open       build, then open the digest
#
#  Writes:  digests/<listing-date>.md   the digest
#           library.md                  running list of every paper surfaced
#           logs/<listing-date>.log     what happened, for unattended runs
# ---------------------------------------------------------------------------

set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || exit 1
mkdir -p logs digests runs

FORCE=""
OPEN=""
NOTIFY=""
for arg in "$@"; do
  case "$arg" in
    --force)  FORCE="--force" ;;
    --open)   OPEN="1" ;;
    --notify) NOTIFY="1" ;;   # macOS notification when done (used by the scheduler)
    *) echo "unknown option: $arg" >&2; exit 64 ;;
  esac
done

notify() {
  [[ -z "$NOTIFY" ]] && return 0
  osascript -e "display notification \"$1\" with title \"arXiv digest\"" \
    >/dev/null 2>&1
}

# shellcheck source=_engine.sh
. "$DIR/_engine.sh"
detect_engine || exit 127

# shellcheck source=_python.sh
. "$DIR/_python.sh"
PY="$(require_python)" || exit 127

say() { printf '%s\n' "$*"; }

# --- 1. fetch and score ----------------------------------------------------
say "Fetching tonight's arXiv announcement..."
FETCH_OUT="$("$PY" fetch.py $FORCE 2>&1)"
FETCH_RC=$?
printf '%s\n' "$FETCH_OUT"

if [[ $FETCH_RC -eq 3 ]]; then
  # already digested this listing — not an error
  EXISTING="$(printf '%s\n' "$FETCH_OUT" | awk '/ALREADY_DIGESTED/{print $2}')"
  say ""
  say "Tonight's listing ($EXISTING) is already digested: digests/$EXISTING.md"
  say "Run './run.sh --force' to rebuild it."
  [[ -n "$OPEN" ]] && open "digests/$EXISTING.md"
  exit 0
fi
if [[ $FETCH_RC -ne 0 ]]; then
  say "Could not fetch the announcement (exit $FETCH_RC). Nothing written."
  exit "$FETCH_RC"
fi

DATE="$(printf '%s\n' "$FETCH_OUT" | awk '/^LISTING_DATE/{print $2}')"
BRIEF="$(printf '%s\n' "$FETCH_OUT" | awk '/^BRIEF/{print $2}')"
if [[ -z "$DATE" || ! -f "$BRIEF" ]]; then
  say "fetch.py produced no briefing. Nothing written."
  exit 1
fi

LOG="logs/$DATE.log"
OUT="digests/$DATE.md"
TMP="runs/$DATE/digest.partial.md"

{
  echo "=== run at $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  printf '%s\n' "$FETCH_OUT"
} >>"$LOG"

# --- 2. read the briefing and write the digest -----------------------------
say "Reading $(grep -c '^### \[' "$BRIEF") shortlisted abstracts with $(engine_name)..."
if ! engine_run "$DIR/prompt.md" "$BRIEF" "$TMP" "$LOG"; then
  say "$(engine_name) exited non-zero; see $LOG. Existing digest left untouched."
  exit 1
fi

# Only replace the digest if we got something that looks like one.
if [[ ! -s "$TMP" ]] || ! grep -q '^# arXiv ' "$TMP"; then
  say "Digest output looked malformed; kept at $TMP rather than overwriting $OUT."
  say "See $LOG for details."
  exit 1
fi
mv "$TMP" "$OUT"
say "Wrote $OUT"

# --- 3. append every surfaced paper to the running library -----------------
"$PY" record.py --date "$DATE" 2>>"$LOG" && say "Updated library.md"

echo "digest written: $OUT" >>"$LOG"
# Terminal preview: the one-line summary plus just the five titles.
say ""
say "----------------------------------------------------------------"
sed -n '3p' "$OUT"
say ""
grep '^### [0-9]' "$OUT" | sed 's/^### /  /'
say "----------------------------------------------------------------"
say "Full digest:  $OUT"
say "Link library: library.md   (tick the ones you download, then run: ./learn.sh)"

FIVE="$(grep -c '^### [0-9]' "$OUT")"
notify "$FIVE papers picked from tonight's listing ($DATE). Digest ready."

[[ -n "$OPEN" ]] && open "$OUT"
exit 0
