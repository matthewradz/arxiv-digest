# Sourced by run.sh and learn.sh — do not run directly.
#
# Finds a Python new enough for tomllib (3.11+). This matters more than it
# looks: macOS still ships /usr/bin/python3 as 3.9, and a GUI-launched app gets
# a minimal PATH where that is the *only* python3 on it. Calling bare `python3`
# would then fail with an ImportError that surfaces as a useless "exit 1".

find_python() {
  local cand
  # Conda is how most scientists get a modern Python, and it is usually in a
  # named environment rather than the base install - a GUI or scheduled launch
  # never sources the shell profile that would put it on PATH, so look in the
  # environment directories directly.
  local conda_envs=()
  local root
  for root in "${CONDA_PREFIX:-}" "${CONDA_ROOT:-}" \
              "$HOME/anaconda3" "$HOME/miniconda3" "$HOME/miniforge3" \
              "$HOME/mambaforge" /opt/anaconda3 /opt/miniconda3 \
              /opt/homebrew/Caskroom/miniforge/base; do
    [[ -n "$root" && -d "$root" ]] || continue
    conda_envs+=("$root/bin/python3")
    local env
    for env in "$root"/envs/*/bin/python3; do
      [[ -x "$env" ]] && conda_envs+=("$env")
    done
  done

  for cand in "${PYTHON:-}" \
              "${conda_envs[@]:-}" \
              /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 \
              /opt/homebrew/bin/python3.12 \
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
