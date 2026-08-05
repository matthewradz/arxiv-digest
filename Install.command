#!/bin/bash
# ---------------------------------------------------------------------------
#  Double-click this file to install the arXiv cond-mat digest.
#
#  It copies everything to ~/arxiv-digest, builds "arXiv Digest.app" in your
#  Applications folder, and offers to run it automatically each weeknight.
#  Nothing is installed system-wide and nothing needs an administrator password.
# ---------------------------------------------------------------------------

cd "$(dirname "$0")" || exit 1

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
dim()  { printf '\033[2m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; }

clear
bold "arXiv cond-mat nightly digest"
dim  "Reads the whole cond-mat listing each night and writes you five papers."
echo

# --- prerequisites ---------------------------------------------------------
bold "Checking what's on this Mac"

PY=""
for cand in "$HOME/anaconda3/bin/python3" "$HOME/miniconda3/bin/python3" \
            /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
            /opt/homebrew/bin/python3 /usr/local/bin/python3 \
            "$(command -v python3 2>/dev/null)" /usr/bin/python3; do
  [[ -n "$cand" && -x "$cand" ]] || continue
  if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
    PY="$cand"; break
  fi
done

if [[ -n "$PY" ]]; then
  ok "Python $("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])') — $PY"
else
  bad "No Python 3.11 or newer found."
  echo
  echo "  macOS only ships Python 3.9, which is too old."
  echo "  Install a current one from https://www.python.org/downloads/"
  echo "  then double-click this installer again."
  echo
  read -r -p "Press return to close. " _
  exit 1
fi

export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"
if command -v claude >/dev/null 2>&1; then
  ok "Claude Code — $(command -v claude)"
elif command -v codex >/dev/null 2>&1; then
  ok "Codex CLI — $(command -v codex)"
else
  bad "No model CLI is installed."
  echo
  echo "  The digest needs one of these to read the abstracts. Install whichever"
  echo "  matches a subscription you already pay for — either works equally well."
  echo
  echo "  If you have ChatGPT Plus or Pro:"
  bold "      npm install -g @openai/codex"
  echo "      then run:  codex     and choose \"Sign in with ChatGPT\""
  echo
  echo "  If you have Claude Pro or Max:"
  bold "      curl -fsSL https://claude.ai/install.sh | bash"
  echo "      then run:  claude    and sign in with your Claude account"
  echo
  echo "  Sign in with the subscription, NOT an API key — an API key bills"
  echo "  separately per use, while the subscription is already paid for."
  echo
  echo "  Then double-click this installer again."
  echo
  read -r -p "Press return to close. " _
  exit 1
fi
echo

# --- install ---------------------------------------------------------------
DEST="$HOME/arxiv-digest"
bold "Installing to $DEST"

if [[ -d "$DEST" && -f "$DEST/fetch.py" ]]; then
  echo "  An existing install is already there."
  echo "  Your digests, picks and edited config.toml will be kept."
  read -r -p "  Update the program files? [Y/n] " ans
  case "${ans:-y}" in
    [nN]*) echo "  Left alone."; KEEP_CONFIG=1; SKIP=1 ;;
    *)     KEEP_CONFIG=1; SKIP="" ;;
  esac
else
  KEEP_CONFIG=""; SKIP=""
fi

if [[ -z "${SKIP:-}" ]]; then
  mkdir -p "$DEST"/{runs,digests,logs}
  for f in fetch.py record.py pick.py learn.py app.py profile.py \
           _python.sh _engine.sh \
           run.sh learn.sh install-app.sh install-schedule.sh package.sh test-fresh.sh \
           config.default.toml prompt.md learn_prompt.md README.md; do
    [[ -f "$f" ]] && cp "$f" "$DEST/$f"
  done
  # config.toml is yours; only create it if it isn't there yet.
  if [[ -f "$DEST/config.toml" ]]; then
    ok "your existing config.toml was left untouched"
  else
    cp config.default.toml "$DEST/config.toml"
    ok "config.toml created from the defaults"
  fi
  # the sample digest, so there is something to read straight away
  if [[ -d digests ]]; then
    cp -R digests/. "$DEST/digests/" 2>/dev/null
    cp -R runs/.    "$DEST/runs/"    2>/dev/null
    [[ -f library.md && ! -f "$DEST/library.md" ]] && cp library.md "$DEST/library.md"
  fi
  chmod +x "$DEST"/*.sh "$DEST"/*.py 2>/dev/null
  ok "program files installed"
fi
echo

# --- the app ---------------------------------------------------------------
bold "Building the app"
if (cd "$DEST" && ./install-app.sh >/dev/null 2>&1); then
  ok "\"arXiv Digest\" is in your Applications folder"
else
  bad "could not build the app — you can still run $DEST/run.sh by hand"
fi
echo

# --- schedule --------------------------------------------------------------
bold "Run it automatically?"
echo "  arXiv publishes Monday to Friday. If you say yes, the digest is built"
echo "  quietly at 9pm on weeknights, so it is already waiting when you look."
read -r -p "  Set that up? [Y/n] " ans
case "${ans:-y}" in
  [nN]*) dim "  Skipped. You can do it later: $DEST/install-schedule.sh" ;;
  *)     if (cd "$DEST" && ./install-schedule.sh 21 0 >/dev/null 2>&1); then
           ok "scheduled for 21:00, Monday to Friday"
           dim "  change it later with: $DEST/install-schedule.sh 20 30"
         else
           bad "could not schedule it; run.sh and the app still work"
         fi ;;
esac
echo

# --- done ------------------------------------------------------------------
bold "Done."
echo
echo "  Open \"arXiv Digest\" from Applications — it walks you through setup:"
echo "  signing in, whose work to follow, and which arXiv sections to read."
echo "  Drag it to your Dock so it is one click away."
echo
echo "  The first digest takes a couple of minutes to build — it reads every"
echo "  abstract in the listing. After that it is instant."
echo
dim  "  The one thing worth doing: click \"Want this\" on the papers you actually"
dim  "  want (click again to undo). After a couple of weeks press \"Retune from"
dim  "  my picks\" and it starts matching your taste. Edit $DEST/config.toml to"
dim  "  change the author list and topics — plain text with comments."
echo
read -r -p "Press return to open it now (or close this window). " _
open -a "arXiv Digest" 2>/dev/null || (cd "$DEST" && "$PY" app.py >/dev/null 2>&1 &)
