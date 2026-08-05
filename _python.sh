# Sourced by run.sh and learn.sh — do not run directly.
#
# Finds a Python new enough for tomllib (3.11+). This matters more than it
# looks: macOS still ships /usr/bin/python3 as 3.9, and a GUI-launched app gets
# a minimal PATH where that is the *only* python3 on it. Calling bare `python3`
# would then fail with an ImportError that surfaces as a useless "exit 1".

find_python() {
  local cand
  for cand in "${PYTHON:-}" \
              "$HOME/anaconda3/bin/python3" "$HOME/miniconda3/bin/python3" \
              /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
              /opt/homebrew/bin/python3 /usr/local/bin/python3 \
              "$(command -v python3 2>/dev/null)" /usr/bin/python3; do
    [[ -n "$cand" && -x "$cand" ]] || continue
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' \
        2>/dev/null; then
      printf '%s' "$cand"
      return 0
    fi
  done
  return 1
}

require_python() {
  local py
  if ! py="$(find_python)"; then
    echo "error: this needs Python 3.11 or newer, and none was found." >&2
    echo "       macOS ships 3.9, which is too old (no tomllib)." >&2
    echo "       Install a newer Python, or set PYTHON=/path/to/python3" >&2
    return 127
  fi
  printf '%s' "$py"
}
