#!/bin/bash
# ---------------------------------------------------------------------------
#  Install (or remove) the nightly schedule.
#
#    ./install-schedule.sh            run every weeknight at 21:00
#    ./install-schedule.sh 20 30      run every weeknight at 20:30
#    ./install-schedule.sh --remove   stop running automatically
#
#  arXiv announces Sunday through Thursday evening ET, which produces the
#  Monday-through-Friday listings — so this schedules Monday to Friday and
#  nothing at the weekend. If a run finds a listing it has already digested it
#  exits quietly without rewriting anything, so an extra run never hurts.
# ---------------------------------------------------------------------------

set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.arxivdigest.nightly"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ "${1:-}" == "--remove" ]]; then
  launchctl unload "$PLIST" 2>/dev/null
  rm -f "$PLIST"
  echo "Removed the nightly schedule. ./run.sh still works by hand."
  exit 0
fi

HOUR="${1:-21}"
MINUTE="${2:-0}"
if ! [[ "$HOUR" =~ ^[0-9]+$ && "$MINUTE" =~ ^[0-9]+$ ]] \
   || (( HOUR > 23 || MINUTE > 59 )); then
  echo "Usage: ./install-schedule.sh [HOUR 0-23] [MINUTE 0-59]" >&2
  exit 64
fi

# launchd runs with a minimal PATH, so pin both the model CLI and the
# interpreter now rather than letting run.sh resolve bare names later.
# shellcheck source=_engine.sh
. "$DIR/_engine.sh"
detect_engine || exit 127
# shellcheck source=_python.sh
. "$DIR/_python.sh"
PY="$(require_python)" || exit 127

mkdir -p "$HOME/Library/LaunchAgents" "$DIR/logs"

# launchd starts with a minimal PATH, so pass in one that can find claude.
ENGINE_DIR="$(dirname "$ENGINE_BIN")"
if [[ "$ENGINE" == "claude" ]]; then ENGINE_VAR=CLAUDE_BIN;
else ENGINE_VAR=CODEX_BIN; fi

{
  cat <<PLIST_HEAD
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$DIR/run.sh</string>
        <string>--notify</string>
    </array>
    <key>WorkingDirectory</key><string>$DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$ENGINE_DIR:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>HOME</key><string>$HOME</string>
        <key>PYTHON</key><string>$PY</string>
        <key>DIGEST_ENGINE</key><string>$ENGINE</string>
        <key>${ENGINE_VAR}</key><string>$ENGINE_BIN</string>
    </dict>
    <key>StartCalendarInterval</key>
    <array>
PLIST_HEAD
  for wd in 1 2 3 4 5; do
    printf '        <dict><key>Weekday</key><integer>%s</integer>' "$wd"
    printf '<key>Hour</key><integer>%s</integer>' "$HOUR"
    printf '<key>Minute</key><integer>%s</integer></dict>\n' "$MINUTE"
  done
  cat <<'PLIST_TAIL'
    </array>
    <key>RunAtLoad</key><false/>
    <key>StandardOutPath</key><string>LOGDIR/scheduler.out.log</string>
    <key>StandardErrorPath</key><string>LOGDIR/scheduler.err.log</string>
</dict>
</plist>
PLIST_TAIL
} | sed "s|LOGDIR|$DIR/logs|g" >"$PLIST"

launchctl unload "$PLIST" 2>/dev/null
if ! launchctl load "$PLIST" 2>&1; then
  echo "launchctl load failed. The plist is at $PLIST" >&2
  exit 1
fi

printf 'Scheduled: every weeknight at %02d:%02d local time.\n' "$HOUR" "$MINUTE"
echo
echo "You will get a notification when each digest is ready."
echo "Read it in $DIR/digests/, or run ./run.sh --open any time."
echo
echo "To stop:  ./install-schedule.sh --remove"
