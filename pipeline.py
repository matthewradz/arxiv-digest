#!/usr/bin/env python3
"""
The nightly pipeline. Works on macOS, Windows and Linux.

    python3 pipeline.py digest              build tonight's digest
    python3 pipeline.py digest --force      rebuild even if it exists
    python3 pipeline.py digest --open       build, then open the digest
    python3 pipeline.py digest --notify     desktop notification when ready
    python3 pipeline.py learn               retune from your recorded picks

run.sh / run.bat and learn.sh / learn.bat are thin wrappers over this, so every
platform runs the same code and there is one implementation to keep correct.
"""

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import engine

HOME = Path(__file__).resolve().parent

# Run straight from a terminal or the scheduler there is no parent to set
# PYTHONIOENCODING, and a Windows console encodes stdout as cp1252 - one Greek
# letter in a title would end the run on a UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def say(*a):
    print(*a, flush=True)


def notify(message, title="arXiv digest"):
    """Desktop notification, best-effort — never fail the run over this."""
    try:
        if sys.platform == "darwin":
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{message}" with title "{title}"'],
                capture_output=True, timeout=15)
        elif sys.platform == "win32":
            ps = (
                "[Windows.UI.Notifications.ToastNotificationManager, "
                "Windows.UI.Notifications, ContentType=WindowsRuntime] > $null;"
                "$t=[Windows.UI.Notifications.ToastNotificationManager]::"
                "GetTemplateContent("
                "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
                f"$t.GetElementsByTagName('text')[0].AppendChild("
                f"$t.CreateTextNode('{title}')) > $null;"
                f"$t.GetElementsByTagName('text')[1].AppendChild("
                f"$t.CreateTextNode('{message}')) > $null;"
                "[Windows.UI.Notifications.ToastNotificationManager]::"
                "CreateToastNotifier('arXiv digest').Show("
                "[Windows.UI.Notifications.ToastNotification]::new($t))")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=20)
    except Exception:
        pass


def open_file(path):
    """Open a file in whatever the platform uses."""
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)], capture_output=True)
        elif sys.platform == "win32":
            import os
            os.startfile(str(path))          # noqa: S606  (Windows-only API)
        else:
            subprocess.run(["xdg-open", str(path)], capture_output=True)
    except Exception:
        pass


def run_python(script, *args, log=None):
    """Run one of our own scripts with the same interpreter we are using."""
    cmd = [sys.executable, str(HOME / script), *args]
    proc = subprocess.run(cmd, cwd=str(HOME), capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          env=engine.env_for_subprocess())
    if log and proc.stderr:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(proc.stderr)
    return proc


# --------------------------------------------------------------------------
#  digest
# --------------------------------------------------------------------------

def digest(force=False, do_open=False, do_notify=False):
    for d in ("logs", "digests", "runs"):
        (HOME / d).mkdir(exist_ok=True)

    say("Fetching tonight's arXiv announcement...")
    proc = run_python("fetch.py", *(["--force"] if force else []))
    out = (proc.stdout or "") + (proc.stderr or "")
    print(out.rstrip())

    # exit 3 means "this listing is already digested" — not an error
    if proc.returncode == 3:
        m = re.search(r"ALREADY_DIGESTED (\S+)", out)
        existing = m.group(1) if m else "today"
        say("")
        say(f"Tonight's listing ({existing}) is already digested: "
            f"digests/{existing}.md")
        say("Rerun with --force to rebuild it.")
        if do_open:
            open_file(HOME / "digests" / f"{existing}.md")
        return 0
    if proc.returncode != 0:
        say(f"Could not fetch the announcement (exit {proc.returncode}). "
            f"Nothing written.")
        return proc.returncode or 1

    date_m = re.search(r"^LISTING_DATE (\S+)", out, re.M)
    brief_m = re.search(r"^BRIEF (.+)$", out, re.M)
    if not date_m or not brief_m or not Path(brief_m.group(1).strip()).exists():
        say("fetch.py produced no briefing. Nothing written.")
        return 1
    date = date_m.group(1)
    brief = Path(brief_m.group(1).strip())

    log = HOME / "logs" / f"{date}.log"
    final = HOME / "digests" / f"{date}.md"
    partial = HOME / "runs" / date / "digest.partial.md"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"=== run at {datetime.now():%Y-%m-%d %H:%M:%S} ===\n{out}\n")

    shortlisted = len(re.findall(r"^### \[", brief.read_text(encoding="utf-8"),
                                 re.M))
    name, _ = engine.detect()
    if not name:
        say(engine.NOT_FOUND)
        return 127
    say(f"Reading {shortlisted} shortlisted abstracts with {name}...")

    ok, name = engine.run(HOME / "prompt.md", brief, partial, log)
    if not ok:
        say(f"{name or 'the model'} exited non-zero; see {log}. "
            f"Existing digest left untouched.")
        return 1

    # Only replace the digest if we got something that looks like one.
    text = partial.read_text(encoding="utf-8") if partial.exists() else ""
    if not text.strip() or not re.search(r"^# arXiv ", text, re.M):
        say(f"Digest output looked malformed; kept at {partial} rather than "
            f"overwriting {final}.")
        say(f"See {log} for details.")
        return 1
    partial.replace(final)
    say(f"Wrote {final.relative_to(HOME)}")

    rec = run_python("record.py", "--date", date, log=log)
    if rec.stdout:
        print(rec.stdout.rstrip())
    if rec.returncode == 0:
        say("Updated library.md")

    # Terminal preview: the one-line summary plus just the five titles.
    lines = text.split("\n")
    titles = [ln[4:] for ln in lines if re.match(r"^### \d", ln)]
    say("")
    say("-" * 64)
    if len(lines) > 2:
        say(lines[2])
    say("")
    for t in titles:
        say(f"  {t}")
    say("-" * 64)
    say(f"Full digest:  {final}")
    say("Link library: library.md   (mark what you want, then run learn)")

    if do_notify:
        notify(f"{len(titles)} papers picked from tonight's listing ({date}).")
    if do_open:
        open_file(final)
    return 0


# --------------------------------------------------------------------------
#  learn
# --------------------------------------------------------------------------

def learn():
    say("Reading the papers you marked as wanted...")
    sync = run_python("pick.py", "--sync")
    print((sync.stdout or "").rstrip())
    if sync.returncode != 0:
        print((sync.stderr or "").rstrip(), file=sys.stderr)
        return 1

    runs = HOME / "runs"
    runs.mkdir(exist_ok=True)
    report = runs / "learn-report.md"
    rep = run_python("learn.py")
    if rep.returncode != 0:
        print((rep.stderr or "").rstrip(), file=sys.stderr)
        return 1
    report.write_text(rep.stdout, encoding="utf-8")

    if rep.stdout.startswith("NO_PICKS"):
        say("")
        say("Nothing marked yet — nothing to learn from.")
        say("Mark a few papers with 'Want this' in the reader (or tick them in "
            "library.md), then run this again.")
        return 0

    say("Analysing your picks...")
    prefs = HOME / "preferences.md"
    combined = runs / "learn-input.md"
    combined.write_text(
        rep.stdout + "\n\n## Current preferences.md\n\n"
        + (prefs.read_text(encoding="utf-8") if prefs.exists() else "(none yet)"),
        encoding="utf-8")

    tmp = runs / "learn-output.md"
    errlog = runs / "learn-error.log"
    ok, name = engine.run(HOME / "learn_prompt.md", combined, tmp, errlog)
    if not ok or not tmp.exists() or not tmp.read_text(encoding="utf-8").strip():
        say(f"{name or 'the model'} produced no output; see {errlog}.")
        return 1

    body = tmp.read_text(encoding="utf-8")
    marker = "===CONFIG-SUGGESTIONS==="
    suggestions = HOME / "config-suggestions.md"
    if marker in body:
        above, _, below = body.partition(marker)
        prefs.write_text(above.strip() + "\n", encoding="utf-8")
        suggestions.write_text(below.strip() + "\n", encoding="utf-8")
    else:
        prefs.write_text(body, encoding="utf-8")
        suggestions.write_text("", encoding="utf-8")

    say("")
    say("Updated preferences.md — tomorrow's digest will use it.")
    say("")
    say("=" * 20 + " suggested config.toml edits " + "=" * 20)
    say(suggestions.read_text(encoding="utf-8").strip() or "(none)")
    say("=" * 69)
    say("")
    say("These are suggestions only; nothing in config.toml was changed.")
    say(f"Full statistics: {report}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    d = sub.add_parser("digest", help="build tonight's digest")
    d.add_argument("--force", action="store_true")
    d.add_argument("--open", action="store_true", dest="do_open")
    d.add_argument("--notify", action="store_true", dest="do_notify")
    sub.add_parser("learn", help="retune from your picks")
    args = ap.parse_args()

    if args.cmd == "learn":
        return learn()
    if args.cmd == "digest":
        return digest(args.force, args.do_open, args.do_notify)
    ap.print_help()
    return 64


if __name__ == "__main__":
    sys.exit(main())
