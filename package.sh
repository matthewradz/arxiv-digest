#!/bin/bash
# ---------------------------------------------------------------------------
#  Build a zip to send to someone else's Mac.
#
#    ./package.sh              writes ~/Desktop/arxiv-digest.zip
#    ./package.sh /some/dir    writes it somewhere else
#
#  The zip contains the program plus both installers (macOS and Windows). It
#  does NOT contain your picks, preferences or logs — they start fresh.
# ---------------------------------------------------------------------------

set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTDIR="${1:-$HOME/Desktop}"
NAME="arxiv-digest"
STAGE="$(mktemp -d)/$NAME"
ZIP="$OUTDIR/$NAME.zip"

trap 'rm -rf "$(dirname "$STAGE")"' EXIT

mkdir -p "$STAGE"/{digests,runs,logs,examples}
cp "$DIR"/examples/*.toml "$STAGE/examples/" 2>/dev/null

for f in fetch.py record.py pick.py learn.py app.py profile.py \
         engine.py pipeline.py schedule.py configedit.py examples.py _python.sh \
         run.sh learn.sh run.bat learn.bat \
         install-app.sh package.sh test-fresh.sh \
         config.default.toml prompt.md learn_prompt.md README.md \
         install_mac.command install_windows.bat; do
  if [[ -f "$DIR/$f" ]]; then
    cp "$DIR/$f" "$STAGE/$f"
  else
    echo "warning: $f is missing" >&2
  fi
done

# No sample digest is shipped, deliberately. The app shows its setup wizard only
# when no digest exists yet, so a bundled sample would skip onboarding — and the
# first real digest, built from the recipient's own config, is worth more than a
# stale one built from someone else's.
#
# Also excluded: config.toml, picks.jsonl, preferences.md, library.md, logs.
# The recipient starts with their own empty history.
chmod +x "$STAGE"/*.sh "$STAGE"/*.py "$STAGE/install_mac.command" 2>/dev/null

# Fail loudly rather than shipping a broken zip: every file the scripts source
# or import must actually be staged.
missing=""
for need in $(grep -ho '\$DIR/_[a-z]*\.sh' "$DIR"/*.sh | sed 's|.*/||' | sort -u); do
  [[ -f "$STAGE/$need" ]] || missing="$missing $need"
done
for need in fetch.py pick.py profile.py record.py learn.py app.py \
            configedit.py examples.py \
            engine.py pipeline.py schedule.py configedit.py examples.py \
            config.default.toml prompt.md learn_prompt.md \
            install_mac.command install_windows.bat; do
  [[ -f "$STAGE/$need" ]] || missing="$missing $need"
done
if [[ -n "$missing" ]]; then
  echo "error: refusing to build — these files are missing:$missing" >&2
  exit 1
fi

cat >"$STAGE/READ ME FIRST.txt" <<'TXT'
arXiv nightly digest
====================

Reads the arXiv sections you choose every weeknight and writes you a short
digest of the few papers worth downloading.


INSTALLING
----------

  On a Mac        double-click   install_mac.command
  On Windows      double-click   install_windows.bat

  Mac: if macOS says it "cannot be opened because it is from an unidentified
  developer", right-click (or control-click) install_mac.command and choose
  Open, then click Open in the dialog. That only happens the first time.

  Windows: if SmartScreen warns you, click "More info" then "Run anyway".

Answer the couple of prompts. Takes under a minute. Then open "arXiv Digest"
(Applications on a Mac, Desktop on Windows) and it walks you through the rest:
signing in, whose work to follow, which arXiv sections, and where to save PDFs.


WHAT YOU NEED FIRST
-------------------

  * Python 3.11 or newer
      Mac ships 3.9, which is too old. Windows usually has none.
      Get one from https://www.python.org/downloads/
      On Windows, tick "Add python.exe to PATH" during setup.

  * ONE of these, to do the reading. Use whichever subscription you already
    pay for - no API key, nothing extra to buy:

      ChatGPT Plus / Pro    npm install -g @openai/codex
                            then run:  codex
                            and choose "Sign in with ChatGPT"

      Claude Pro / Max      Mac:      curl -fsSL https://claude.ai/install.sh | bash
                            Windows:  irm https://claude.ai/install.ps1 | iex
                            then run:  claude    and sign in

The installer checks both and tells you exactly what to do if either is
missing. Everything ends up in your home folder under arxiv-digest, nothing is
installed system-wide, and no administrator password is needed.

Full details are in README.md.
TXT

mkdir -p "$OUTDIR"
rm -f "$ZIP"
(cd "$(dirname "$STAGE")" && zip -q -r -X "$ZIP" "$NAME" \
   -x '*__pycache__*' '*.DS_Store' '*.app-port') || {
  echo "zip failed" >&2; exit 1; }

SIZE="$(du -h "$ZIP" | cut -f1 | tr -d ' ')"
echo
echo "Wrote $ZIP  ($SIZE)"
echo
echo "Send it however is easiest — email, AirDrop, Dropbox, iMessage."
echo "They unzip it and double-click install_mac.command or install_windows.bat."
