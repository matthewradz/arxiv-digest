#!/usr/bin/env python3
"""
Find and drive whichever model CLI is installed, on any platform.

The digest needs a model to read the abstracts. Either coding CLI works, because
both take a prompt plus piped stdin and print the result:

    Claude Code   claude -p "prompt" < input          (Claude Pro/Max)
    Codex CLI     codex exec "prompt" < input         (ChatGPT Plus/Pro)

Force one with DIGEST_ENGINE=claude|codex. Pick a model with DIGEST_MODEL.

    python3 engine.py --check      report what is installed and usable
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path(__file__).resolve().parent

NOT_FOUND = """No model CLI found. Install whichever matches the subscription you have:

  ChatGPT Plus/Pro    npm install -g @openai/codex
                      then run:  codex     and choose "Sign in with ChatGPT"

  Claude Pro/Max      macOS/Linux:  curl -fsSL https://claude.ai/install.sh | bash
                      Windows:      irm https://claude.ai/install.ps1 | iex
                      then run:  claude    and sign in

Either one works. Sign in with the subscription, not an API key."""


def search_path(windows=None):
    """
    PATH plus the places these CLIs install to that a GUI or scheduled launch
    misses. Built from plain strings rather than Path objects: pathlib refuses
    to construct a WindowsPath on posix, which would make this untestable and
    adds nothing here.
    """
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    windows = os.name == "nt" if windows is None else windows
    home = os.path.expanduser("~")
    if windows:
        appdata = os.environ.get("APPDATA", "")
        extra = [os.path.join(home, "AppData", "Local", "Programs"),
                 os.path.join(home, "AppData", "Roaming", "npm"),
                 os.path.join(home, ".local", "bin")]
        if appdata:
            extra.append(os.path.join(appdata, "npm"))
    else:
        extra = [os.path.join(home, ".local", "bin"), "/usr/local/bin",
                 "/opt/homebrew/bin", "/usr/bin", "/bin"]
    for s in extra:
        if s and s not in parts:
            parts.append(s)
    return os.pathsep.join(parts)


def find(exe):
    """Locate a CLI, honouring an explicit override first."""
    override = os.environ.get(f"{exe.upper()}_BIN")
    if override and Path(override).exists():
        return override
    return shutil.which(exe, path=search_path())


def detect():
    """Return (engine_name, path) or (None, None)."""
    want = os.environ.get("DIGEST_ENGINE", "").strip().lower()
    if want:
        if want not in ("claude", "codex"):
            print(f"DIGEST_ENGINE must be 'claude' or 'codex', not {want!r}",
                  file=sys.stderr)
            return None, None
        found = find(want)
        if not found:
            print(f"DIGEST_ENGINE={want} but {want} is not installed.",
                  file=sys.stderr)
            return None, None
        return want, found
    for name in ("claude", "codex"):
        found = find(name)
        if found:
            return name, found
    return None, None


def env_for_subprocess():
    """
    A GUI- or scheduler-launched process inherits almost nothing.

    USER and LOGNAME matter more than they look: without USER the model CLI
    cannot find its stored login and fails with "Not logged in", which reads
    like an auth problem but is a missing environment variable.
    """
    env = dict(os.environ)
    env["PATH"] = search_path()
    if os.name != "nt":
        import getpass
        try:
            who = getpass.getuser()
        except Exception:
            who = ""
        if who:
            env.setdefault("USER", who)
            env.setdefault("LOGNAME", who)
    env["PYTHON"] = sys.executable
    return env


def run(prompt_file, stdin_file, out_file, log_file, timeout=1800):
    """
    Feed a prompt plus a briefing to the model; write its answer to out_file.

    Returns (ok, engine_name). Never raises on a CLI failure — the caller
    decides what to do, and the existing digest must survive a bad run.
    """
    name, exe = detect()
    if not name:
        Path(log_file).open("a", encoding="utf-8").write(NOT_FOUND + "\n")
        return False, None

    prompt = Path(prompt_file).read_text(encoding="utf-8")
    model = os.environ.get("DIGEST_MODEL") or os.environ.get("ARXIV_DIGEST_MODEL")
    out_path = Path(out_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if name == "claude":
        cmd = [exe, "-p", prompt] + (["--model", model] if model else [])
        capture_stdout = True
    else:
        # --skip-git-repo-check: this folder is not a git repo.
        # --ephemeral: leave no session files behind.
        # -o: write the final message to a file; codex also streams progress to
        #     stdout, so take the answer from the file rather than the stream.
        # The sandbox is read-only by default, which is what we want.
        cmd = [exe, "exec", "--skip-git-repo-check", "--ephemeral"]
        if model:
            cmd += ["-m", model]
        cmd += ["-o", str(out_path), prompt]
        capture_stdout = False

    try:
        with open(stdin_file, "rb") as fin, \
                open(log_file, "ab") as flog:
            proc = subprocess.run(
                cmd, stdin=fin,
                stdout=subprocess.PIPE if capture_stdout else flog,
                stderr=flog, env=env_for_subprocess(), cwd=str(HOME),
                timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        with open(log_file, "a", encoding="utf-8") as flog:
            flog.write(f"{name} failed: {exc}\n")
        return False, name

    if capture_stdout:
        out_path.write_bytes(proc.stdout or b"")
    return proc.returncode == 0, name


def main():
    name, exe = detect()
    if not name:
        print(NOT_FOUND)
        return 127
    print(f"{name} -> {exe}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
