#!/bin/bash
# ---------------------------------------------------------------------------
#  Double-click this file to install the arXiv digest on macOS.
#  (Windows users: run install_windows.bat instead.)
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
bold "arXiv nightly digest"
dim  "Reads the arXiv sections you choose each night, and writes you five papers."
echo

# --- prerequisites ---------------------------------------------------------
bold "Checking what's on this Mac"

# Whatever the user activated wins. Checking the fixed locations first meant
# `conda activate myenv` was silently ignored in favour of the base env.
PY=""
for cand in "${PYTHON:-}" \
            "${CONDA_PREFIX:-}/bin/python3" \
            "${VIRTUAL_ENV:-}/bin/python3" "${VIRTUAL_ENV:-}/bin/python" \
            "$(command -v python3 2>/dev/null)" \
            "$HOME/anaconda3/bin/python3" "$HOME/miniconda3/bin/python3" \
            "$HOME/miniforge3/bin/python3" \
            /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
            /opt/homebrew/bin/python3 /usr/local/bin/python3 \
            /usr/bin/python3; do
  [[ -n "$cand" && -x "$cand" ]] || continue
  if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
    PY="$cand"; break
  fi
done

if [[ -n "$PY" ]]; then
  ok "Python $("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])') — $PY"
  if [[ -n "${CONDA_PREFIX:-}" && "$PY" == "$CONDA_PREFIX"* ]]; then
    dim "       (from the active conda environment: $(basename "$CONDA_PREFIX"))"
  elif [[ -n "${VIRTUAL_ENV:-}" && "$PY" == "$VIRTUAL_ENV"* ]]; then
    dim "       (from the active virtual environment: $(basename "$VIRTUAL_ENV"))"
  fi
else
  bad "No Python 3.11 or newer found."
  echo
  echo "  macOS only ships Python 3.9, which is too old."
  echo
  echo "  If yours is in a conda or virtual environment, this installer cannot"
  echo "  see it when launched from Finder — Finder does not activate anything."
  echo "  Run it from a terminal with the environment active instead:"
  echo
  bold "      conda activate myenv"
  bold "      bash \"$(pwd)/install_mac.command\""
  echo
  echo "  Or install a system-wide Python from https://www.python.org/downloads/"
  echo "  and double-click this installer again."
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
  mkdir -p "$DEST"/{runs,digests,logs,examples}
  cp examples/*.toml "$DEST/examples/" 2>/dev/null
  for f in fetch.py record.py pick.py learn.py app.py profile.py \
           engine.py pipeline.py schedule.py configedit.py examples.py _python.sh \
           run.sh learn.sh run.bat learn.bat \
           install-app.sh package.sh test-fresh.sh \
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
  # Remember which interpreter to use. The nightly job cannot run
  # `conda activate`, so it needs the absolute path — and because the tool is
  # standard-library only, the path alone is enough.
  printf '%s\n' "$PY" > "$DEST/.python-path"
  ok "program files installed"
  dim "       using $PY (recorded in .python-path — edit to change)"
fi
echo

# --- the app ---------------------------------------------------------------
bold "Building the app"
APP_OUT="$(cd "$DEST" && ./install-app.sh 2>&1)"
APP_PATH="$(printf '%s\n' "$APP_OUT" | awk '/^INSTALLED_AT /{ $1=""; sub(/^ /,""); print }')"
if [[ -n "$APP_PATH" && -d "$APP_PATH" ]]; then
  ok "app installed at:"
  echo "        $APP_PATH"
else
  bad "could not build the app. The reason was:"
  printf '%s\n' "$APP_OUT" | sed 's/^/        /'
  echo "        You can still start it with:  $PY $DEST/app.py"
fi
echo

# --- schedule --------------------------------------------------------------
bold "How should it run?"
echo
echo "  Either way, opening the app always builds the digest if it is not"
echo "  already there. The only question is whether it also runs on its own."
echo
echo "    automatic  — built quietly at 9pm Mon-Fri, so it is waiting for you,"
echo "                 with a notification when it is ready. Opening the app"
echo "                 is then instant."
echo "    on demand  — nothing runs in the background. Opening the app builds"
echo "                 that evening's digest, which takes 2-3 minutes."
echo
read -r -p "  Set up the automatic 9pm run? [Y/n] " ans
case "${ans:-y}" in
  [nN]*) dim "  On demand only. Turn it on later with:"
         dim "    python3 $DEST/schedule.py install" ;;
  *)     if (cd "$DEST" && "$PY" schedule.py install 21 0 >/dev/null 2>&1); then
           ok "scheduled for 21:00, Monday to Friday"
           dim "  change the time:  python3 $DEST/schedule.py install 20 30"
           dim "  turn it off:      python3 $DEST/schedule.py remove"
         else
           bad "could not schedule it; run.sh and the app still work"
         fi ;;
esac
echo

# --- done ------------------------------------------------------------------
bold "Done."
echo
if [[ -n "${APP_PATH:-}" ]]; then
  echo "  Open it from:"
  bold "      $APP_PATH"
  case "$APP_PATH" in
    "$HOME/Applications"*)
      echo "  Note: that is the Applications folder in your HOME folder, which is"
      echo "  not the one in Finder's sidebar. To get there: Finder > Go >"
      echo "  Go to Folder, then type  ~/Applications" ;;
  esac
else
  echo "  Start it with:  $PY $DEST/app.py"
fi
echo "  It walks you through setup: signing in, whose work to follow, which"
echo "  arXiv sections, and where to save papers."
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
if [[ -n "${APP_PATH:-}" && -d "$APP_PATH" ]]; then
  open "$APP_PATH"
else
  (cd "$DEST" && "$PY" app.py >/dev/null 2>&1 &)
fi
