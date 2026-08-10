#!/bin/bash
# ---------------------------------------------------------------------------
#  Try the first-run experience without losing anything.
#
#    ./test-fresh.sh start     stash your data, reset to defaults, open the app
#    ./test-fresh.sh restore   put everything back exactly as it was
#    ./test-fresh.sh status    show what is currently stashed
#
#  Your config, picks, digests and library are moved into .stash/ — nothing is
#  deleted, and 'restore' returns them. Useful for checking what someone else
#  will see the first time they open it.
# ---------------------------------------------------------------------------

set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || exit 1
STASH="$DIR/.stash"

# Everything that makes this install "yours".
ITEMS=(config.toml picks.jsonl preferences.md config-suggestions.md
       profile-suggestions.toml library.md .setup-done digests runs logs
       config.toml.backup)

# Matching only "$DIR/app.py" misses a reader started as `python3 app.py` from
# inside the folder, which then keeps serving stale state. Match both, but stay
# scoped to this directory so we never kill someone else's unrelated app.py.
stop_readers() {
  pkill -f "$DIR/app.py" 2>/dev/null
  for pid in $(pgrep -f '[a]pp\.py' 2>/dev/null); do
    if [[ "$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | tail -1)" == "n$DIR" ]]; then
      kill "$pid" 2>/dev/null
    fi
  done
  return 0
}

case "${1:-}" in
start)
  if [[ -d "$STASH" ]]; then
    echo "Already stashed. Run './test-fresh.sh restore' first." >&2
    exit 1
  fi
  mkdir -p "$STASH"
  moved=0
  for i in "${ITEMS[@]}"; do
    if [[ -e "$i" ]]; then
      mv "$i" "$STASH/" && moved=$((moved + 1))
    fi
  done
  mkdir -p digests runs logs
  cp config.default.toml config.toml
  echo "Stashed $moved item(s) into .stash/ and reset to defaults."
  echo
  echo "The app will now behave exactly as it would for a new user:"
  echo "  setup wizard -> sign in -> whose work -> which sections -> first build."
  echo
  echo "When you are done:  ./test-fresh.sh restore"
  echo
  stop_readers
  rm -f runs/.app-port
  open -a "arXiv Digest" 2>/dev/null \
    || echo "Open the app yourself, or run: python3 app.py"
  ;;

restore)
  if [[ ! -d "$STASH" ]]; then
    echo "Nothing is stashed." >&2
    exit 1
  fi
  stop_readers
  sleep 1
  # Throw away whatever the test run produced, then put the originals back.
  # Anything present now that was NOT stashed was created by the test and did
  # not exist beforehand, so it has to go too — otherwise files like
  # .setup-done leak into the real install and "back as it was" is a lie.
  for i in "${ITEMS[@]}"; do
    if [[ -e "$i" ]]; then
      [[ -e "$STASH/$i" ]] || echo "  discarding test artefact: $i"
      rm -rf "$i"
    fi
  done
  restored=0
  for i in "${ITEMS[@]}"; do
    if [[ -e "$STASH/$i" ]]; then
      mv "$STASH/$i" "$DIR/" && restored=$((restored + 1))
    fi
  done
  rm -f runs/.app-port
  rmdir "$STASH" 2>/dev/null || {
    echo "note: .stash/ still has files in it:"; ls -A "$STASH"; }
  echo "Restored $restored item(s). Your install is back as it was."
  ;;

status)
  if [[ -d "$STASH" ]]; then
    echo "A test run is in progress. Stashed:"
    ls -A "$STASH" | sed 's/^/  /'
    echo
    echo "Restore with: ./test-fresh.sh restore"
  else
    echo "Nothing stashed — this is your normal install."
    echo "  config.toml   $(test -f config.toml && echo present || echo MISSING)"
    echo "  digests       $(ls digests 2>/dev/null | wc -l | tr -d ' ') file(s)"
    echo "  picks         $(wc -l <picks.jsonl 2>/dev/null | tr -d ' ' || echo 0)"
  fi
  ;;

*)
  echo "Usage: ./test-fresh.sh {start|restore|status}" >&2
  exit 64
  ;;
esac
