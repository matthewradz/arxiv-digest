# Sourced by run.sh and learn.sh — do not run directly.
#
# The digest needs a model to read the abstracts. It can use either coding CLI,
# whichever is installed, because both take a prompt plus piped stdin and print
# the result:
#
#   Claude Code   claude -p "prompt" < input          (Claude Pro/Max, or API key)
#   Codex CLI     codex exec "prompt" < input         (ChatGPT Plus/Pro, or API key)
#
# Force one with  DIGEST_ENGINE=claude  or  DIGEST_ENGINE=codex.
# Pick a model with DIGEST_MODEL.

ENGINE=""
ENGINE_BIN=""

detect_engine() {
  local want="${DIGEST_ENGINE:-}"

  if [[ -n "$want" ]]; then
    case "$want" in
      claude) ENGINE_BIN="${CLAUDE_BIN:-$(command -v claude 2>/dev/null)}" ;;
      codex)  ENGINE_BIN="${CODEX_BIN:-$(command -v codex 2>/dev/null)}" ;;
      *) echo "error: DIGEST_ENGINE must be 'claude' or 'codex', not '$want'" >&2
         return 1 ;;
    esac
    if [[ -z "$ENGINE_BIN" ]]; then
      echo "error: DIGEST_ENGINE=$want but '$want' is not installed." >&2
      return 1
    fi
    ENGINE="$want"
    return 0
  fi

  ENGINE_BIN="${CLAUDE_BIN:-$(command -v claude 2>/dev/null)}"
  if [[ -n "$ENGINE_BIN" ]]; then ENGINE="claude"; return 0; fi

  ENGINE_BIN="${CODEX_BIN:-$(command -v codex 2>/dev/null)}"
  if [[ -n "$ENGINE_BIN" ]]; then ENGINE="codex"; return 0; fi

  cat >&2 <<'MSG'
error: no model CLI found. Install whichever matches the subscription you have:

  Claude Pro/Max      curl -fsSL https://claude.ai/install.sh | bash
                      then run:  claude          and sign in

  ChatGPT Plus/Pro    npm install -g @openai/codex
                      then run:  codex           and choose "Sign in with ChatGPT"

Either one works. Nothing else needs to change.
MSG
  return 1
}

engine_name() { printf '%s' "$ENGINE"; }

# engine_run <prompt-file> <stdin-file> <output-file> <log-file>
# Returns non-zero on failure. The output file is only written by the engine.
engine_run() {
  local prompt_file="$1" in_file="$2" out_file="$3" log="$4"
  local prompt rc
  prompt="$(cat "$prompt_file")"

  case "$ENGINE" in
    claude)
      if [[ -n "${DIGEST_MODEL:-${ARXIV_DIGEST_MODEL:-}}" ]]; then
        "$ENGINE_BIN" -p "$prompt" \
          --model "${DIGEST_MODEL:-$ARXIV_DIGEST_MODEL}" \
          <"$in_file" >"$out_file" 2>>"$log"
      else
        "$ENGINE_BIN" -p "$prompt" <"$in_file" >"$out_file" 2>>"$log"
      fi
      rc=$?
      ;;
    codex)
      # --skip-git-repo-check: this folder is not a git repo.
      # --ephemeral: don't leave session files behind.
      # -o: write the final message to the file. codex also streams progress,
      #     so send its stdout to the log and take the answer from the file.
      # The sandbox is read-only by default, which is what we want — the engine
      # should read the briefing and write prose, nothing else.
      if [[ -n "${DIGEST_MODEL:-}" ]]; then
        "$ENGINE_BIN" exec --skip-git-repo-check --ephemeral \
          -m "$DIGEST_MODEL" -o "$out_file" "$prompt" \
          <"$in_file" >>"$log" 2>>"$log"
      else
        "$ENGINE_BIN" exec --skip-git-repo-check --ephemeral \
          -o "$out_file" "$prompt" <"$in_file" >>"$log" 2>>"$log"
      fi
      rc=$?
      ;;
    *)
      echo "error: no engine detected; call detect_engine first" >&2
      return 1
      ;;
  esac
  return $rc
}
