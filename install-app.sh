#!/bin/bash
# ---------------------------------------------------------------------------
#  Build "arXiv Digest.app" — a double-clickable launcher for the reader.
#
#    ./install-app.sh              install to ~/Applications
#    ./install-app.sh /Applications
#
#  The app is a thin wrapper: it finds a usable Python and runs app.py. All the
#  real work still happens in run.sh, so nothing here is load-bearing and you
#  can delete the app without breaking anything.
# ---------------------------------------------------------------------------

set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Prefer /Applications: Finder's sidebar "Applications" shows /Applications,
# not ~/Applications, so installing to the home folder makes the app look like
# it was never installed. Fall back to ~/Applications when /Applications is not
# writable (no admin rights), and say which one was used.
if [[ -n "${1:-}" ]]; then
  DEST="$1"
elif [[ -w "/Applications" ]]; then
  DEST="/Applications"
else
  DEST="$HOME/Applications"
fi
APP="$DEST/arXiv Digest.app"

mkdir -p "$DEST" || { echo "cannot write to $DEST" >&2; exit 1; }
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

cat >"$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>arXiv Digest</string>
    <key>CFBundleDisplayName</key><string>arXiv Digest</string>
    <key>CFBundleIdentifier</key><string>com.arxivdigest.reader</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleExecutable</key><string>arXivDigest</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# The launcher. GUI-launched apps get almost no PATH and macOS's own
# /usr/bin/python3 may be too old for tomllib, so search explicitly.
cat >"$APP/Contents/MacOS/arXivDigest" <<LAUNCHER
#!/bin/bash
DIGEST_DIR="$DIR"
LAUNCHER
cat >>"$APP/Contents/MacOS/arXivDigest" <<'LAUNCHER'
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

fail() {
  osascript -e "display dialog \"$1\" with title \"arXiv Digest\" buttons {\"OK\"} default button 1 with icon caution" >/dev/null 2>&1
  exit 1
}

# Find a Python that is at least 3.11 (tomllib).
# .python-path is what the installer recorded, and it wins: it may point into
# a conda or virtual environment that this launcher cannot activate. Because
# the tool is standard-library only, the interpreter path alone is enough.
PY=""
RECORDED=""
[[ -f "$DIGEST_DIR/.python-path" ]] && RECORDED="$(head -n1 "$DIGEST_DIR/.python-path" | tr -d '[:space:]')"
for cand in "$RECORDED" "${PYTHON:-}" \
            "${CONDA_PREFIX:-}/bin/python3" \
            "${VIRTUAL_ENV:-}/bin/python3" \
            "$HOME/anaconda3/bin/python3" "$HOME/miniconda3/bin/python3" \
            "$HOME/miniforge3/bin/python3" \
            /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
            /opt/homebrew/bin/python3 /usr/local/bin/python3 \
            "$(command -v python3 2>/dev/null)" /usr/bin/python3; do
  [[ -x "$cand" ]] || continue
  if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
    PY="$cand"; break
  fi
done
[[ -n "$PY" ]] || fail "Python 3.11 or newer is needed but was not found. If yours is in a conda or virtual environment, write its full path into $DIGEST_DIR/.python-path"

[[ -d "$DIGEST_DIR" ]] || fail "The digest folder is missing: $DIGEST_DIR"

if ! command -v claude >/dev/null 2>&1 && ! command -v codex >/dev/null 2>&1; then
  fail "No model CLI found. Install Claude Code (Claude Pro/Max) or Codex CLI (ChatGPT Plus/Pro), sign in, then open this app again."
fi

cd "$DIGEST_DIR" || fail "Cannot open $DIGEST_DIR"
exec "$PY" "$DIGEST_DIR/app.py" >>"$DIGEST_DIR/logs/app.log" 2>&1
LAUNCHER

chmod +x "$APP/Contents/MacOS/arXivDigest"
mkdir -p "$DIR/logs"

# Nudge Finder/LaunchServices to notice the new bundle.
touch "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
  -f "$APP" >/dev/null 2>&1

echo "INSTALLED_AT $APP"
echo "Installed: $APP"
echo
echo "Double-click it to build and read tonight's digest."
echo "Drag it to the Dock so it is one click away."
echo
echo "It opens in your browser, and quits by itself once you stop reading."
