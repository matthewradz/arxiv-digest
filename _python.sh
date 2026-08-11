# Sourced by run.sh and learn.sh — do not run directly.
#
# Finds a Python new enough for tomllib (3.11+).
#
# Order matters, and the rule is: whatever the user has chosen wins.
#
#   1. $PYTHON                  explicit override, always final
#   2. .python-path             what the installer recorded (see below)
#   3. $CONDA_PREFIX            the conda env that is currently activated
#   4. $VIRTUAL_ENV             the venv that is currently activated
#   5. python3 on PATH          which also respects an activated env
#   6. common install locations last-resort fallbacks
#
# Checking the fallbacks before PATH was a real bug: someone with 3.12 in a
# conda env and 3.11 in base would silently get base, and someone whose base
# env was broken got a broken interpreter despite having activated a good one.
#
# The tool is standard-library only, so it never needs an environment to be
# *activated* at runtime — it only needs the absolute path to that
# interpreter. That matters because the nightly scheduled job cannot run
# `conda activate`, so the path is recorded in .python-path at install time
# and reused from then on.

find_python() {
  local cand recorded
  recorded=""
  if [[ -f "${DIR:-.}/.python-path" ]]; then
    recorded="$(head -n1 "${DIR:-.}/.python-path" | tr -d '[:space:]')"
  fi

  for cand in "${PYTHON:-}" \
              "$recorded" \
              "${CONDA_PREFIX:-}/bin/python3" \
              "${VIRTUAL_ENV:-}/bin/python3" \
              "${VIRTUAL_ENV:-}/bin/python" \
              "$(command -v python3 2>/dev/null)" \
              "$HOME/anaconda3/bin/python3" "$HOME/miniconda3/bin/python3" \
              "$HOME/miniforge3/bin/python3" \
              /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
              /opt/homebrew/bin/python3 /usr/local/bin/python3 \
              /usr/bin/python3; do
    [[ -n "$cand" && "$cand" != "/bin/python3" && -x "$cand" ]] || continue
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
    echo "" >&2
    echo "       If your Python lives in a conda or virtual environment," >&2
    echo "       activate it and run this again:" >&2
    echo "           conda activate myenv && ./run.sh" >&2
    echo "" >&2
    echo "       Or point at it directly, which needs no activation:" >&2
    echo "           PYTHON=/path/to/python3 ./run.sh" >&2
    return 127
  fi
  printf '%s' "$py"
}
