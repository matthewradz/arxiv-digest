#!/bin/bash
# ---------------------------------------------------------------------------
#  Build a zip to send to someone else's Mac.
#
#    ./package.sh              writes ~/Desktop/arxiv-digest.zip
#    ./package.sh /some/dir    writes it somewhere else
#
#  The zip contains the program, a sample digest, and Install.command. It does
#  NOT contain your own picks, preferences or logs — the recipient starts fresh.
# ---------------------------------------------------------------------------

set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTDIR="${1:-$HOME/Desktop}"
NAME="arxiv-digest"
STAGE="$(mktemp -d)/$NAME"
ZIP="$OUTDIR/$NAME.zip"

trap 'rm -rf "$(dirname "$STAGE")"' EXIT

mkdir -p "$STAGE"/{digests,runs,logs}

for f in fetch.py record.py pick.py learn.py app.py profile.py \
         _python.sh _engine.sh \
         run.sh learn.sh install-app.sh install-schedule.sh package.sh \
         config.default.toml prompt.md learn_prompt.md README.md Install.command; do
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
chmod +x "$STAGE"/*.sh "$STAGE"/*.py "$STAGE/Install.command" 2>/dev/null

# Fail loudly rather than shipping a broken zip: every file the scripts source
# or import must actually be staged.
missing=""
for need in $(grep -ho '\$DIR/_[a-z]*\.sh' "$DIR"/*.sh | sed 's|.*/||' | sort -u); do
  [[ -f "$STAGE/$need" ]] || missing="$missing $need"
done
for need in fetch.py pick.py profile.py record.py learn.py app.py \
            config.default.toml prompt.md learn_prompt.md; do
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

1.  Move this whole folder somewhere sensible (your home folder is fine).

2.  Double-click  Install.command

    If macOS says it "cannot be opened because it is from an unidentified
    developer", right-click (or control-click) Install.command and choose
    Open, then click Open in the dialog. That only happens the first time.

3.  Answer the two prompts. Takes under a minute.

4.  Open "arXiv Digest" from your Applications folder. It walks you through
    the rest: signing in, whose work to follow, and which arXiv sections
    to read.

One prerequisite the installer cannot fix for you:

  * Python 3.11 or newer  —  macOS ships 3.9, which is too old.
                             Get one from https://www.python.org/downloads/

You also need ONE of these, to do the reading. Use whichever subscription you
already pay for — no API key, nothing extra to buy. The setup screen checks
this and tells you exactly what to do:

  * ChatGPT Plus / Pro    npm install -g @openai/codex
                          then run `codex` and choose "Sign in with ChatGPT"

  * Claude Pro / Max      curl -fsSL https://claude.ai/install.sh | bash
                          then run `claude` and sign in

Everything ends up in ~/arxiv-digest, and an app called "arXiv Digest" appears
in your Applications folder. Nothing is installed system-wide, and no
administrator password is needed.

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
echo "He unzips it, double-clicks Install.command, and answers two prompts."
