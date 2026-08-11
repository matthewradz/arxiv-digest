#!/usr/bin/env python3
"""
Run the digest automatically on weeknights — launchd on macOS, Task Scheduler
on Windows, cron on Linux.

    python3 schedule.py install          every weeknight at 21:00
    python3 schedule.py install 20 30    at 20:30 instead
    python3 schedule.py remove           back to on-demand only
    python3 schedule.py status           is it scheduled, and when

arXiv announces Sunday through Thursday evening, which produces the Monday to
Friday listings, so nothing is scheduled at the weekend. A run that finds a
listing it has already digested exits quietly, so an extra run never hurts.
"""

import getpass
import subprocess
import sys
from pathlib import Path

HOME = Path(__file__).resolve().parent
LABEL = "com.arxivdigest.nightly"
TASK = "arXiv digest nightly"


def plist_path():
    return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


# --------------------------------------------------------------------------
#  macOS — launchd
# --------------------------------------------------------------------------

def mac_install(hour, minute):
    import engine
    name, exe = engine.detect()
    if not name:
        print(engine.NOT_FOUND, file=sys.stderr)
        return 127

    who = getpass.getuser()
    entries = "".join(
        f"        <dict><key>Weekday</key><integer>{d}</integer>"
        f"<key>Hour</key><integer>{hour}</integer>"
        f"<key>Minute</key><integer>{minute}</integer></dict>\n"
        for d in range(1, 6))
    logs = HOME / "logs"
    logs.mkdir(exist_ok=True)

    # USER and LOGNAME are essential, not cosmetic: launchd does not set them,
    # and without USER the model CLI cannot find its stored login and dies with
    # "Not logged in", which looks like an auth failure but is a missing var.
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>{HOME / 'pipeline.py'}</string>
        <string>digest</string>
        <string>--notify</string>
    </array>
    <key>WorkingDirectory</key><string>{HOME}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>{engine.search_path()}</string>
        <key>HOME</key><string>{Path.home()}</string>
        <key>USER</key><string>{who}</string>
        <key>LOGNAME</key><string>{who}</string>
        <key>{'CLAUDE_BIN' if name == 'claude' else 'CODEX_BIN'}</key>
        <string>{exe}</string>
        <key>DIGEST_ENGINE</key><string>{name}</string>
    </dict>
    <key>StartCalendarInterval</key>
    <array>
{entries}    </array>
    <key>RunAtLoad</key><false/>
    <key>StandardOutPath</key><string>{logs / 'scheduler.out.log'}</string>
    <key>StandardErrorPath</key><string>{logs / 'scheduler.err.log'}</string>
</dict>
</plist>
"""
    p = plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(plist, encoding="utf-8")
    subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
    r = subprocess.run(["launchctl", "load", str(p)], capture_output=True,
                       text=True)
    if r.returncode != 0:
        print(f"launchctl load failed: {r.stderr.strip()}", file=sys.stderr)
        return 1
    return 0


def mac_remove():
    p = plist_path()
    subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
    p.unlink(missing_ok=True)
    return 0


def mac_status():
    r = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    return LABEL in (r.stdout or "")


# --------------------------------------------------------------------------
#  Windows — Task Scheduler
# --------------------------------------------------------------------------

def win_pythonw():
    """Prefer pythonw.exe so the nightly run does not flash a console window."""
    exe = Path(sys.executable)
    quiet = exe.with_name("pythonw.exe")
    return str(quiet if quiet.exists() else exe)


def win_install(hour, minute):
    cmd = [
        "schtasks", "/Create", "/F",
        "/TN", TASK,
        "/SC", "WEEKLY",
        "/D", "MON,TUE,WED,THU,FRI",
        "/ST", f"{hour:02d}:{minute:02d}",
        "/TR", f'"{win_pythonw()}" "{HOME / "pipeline.py"}" digest --notify',
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print((r.stderr or r.stdout).strip(), file=sys.stderr)
        return 1
    return 0


def win_remove():
    subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"],
                   capture_output=True)
    return 0


def win_status():
    r = subprocess.run(["schtasks", "/Query", "/TN", TASK],
                       capture_output=True, text=True)
    return r.returncode == 0


# --------------------------------------------------------------------------
#  Linux — cron
# --------------------------------------------------------------------------

MARK = "# arxiv-digest nightly"


def _crontab(text=None):
    if text is None:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else ""
    subprocess.run(["crontab", "-"], input=text, text=True,
                   capture_output=True)
    return text


def linux_install(hour, minute):
    lines = [l for l in _crontab().splitlines() if MARK not in l]
    lines.append(f'{minute} {hour} * * 1-5 cd "{HOME}" && '
                 f'"{sys.executable}" pipeline.py digest '
                 f'>> "{HOME}/logs/scheduler.out.log" 2>&1  {MARK}')
    _crontab("\n".join(lines) + "\n")
    return 0


def linux_remove():
    _crontab("\n".join(l for l in _crontab().splitlines()
                       if MARK not in l) + "\n")
    return 0


def linux_status():
    return MARK in _crontab()


# --------------------------------------------------------------------------

def backend():
    if sys.platform == "darwin":
        return mac_install, mac_remove, mac_status, "launchd"
    if sys.platform == "win32":
        return win_install, win_remove, win_status, "Task Scheduler"
    return linux_install, linux_remove, linux_status, "cron"


def main():
    args = sys.argv[1:]
    action = args[0] if args else "status"
    install, remove, status, what = backend()

    if action == "install":
        try:
            hour = int(args[1]) if len(args) > 1 else 21
            minute = int(args[2]) if len(args) > 2 else 0
        except ValueError:
            print("Usage: schedule.py install [HOUR 0-23] [MINUTE 0-59]",
                  file=sys.stderr)
            return 64
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            print("Hour must be 0-23 and minute 0-59.", file=sys.stderr)
            return 64
        rc = install(hour, minute)
        if rc == 0:
            print(f"Scheduled with {what}: every weeknight at "
                  f"{hour:02d}:{minute:02d}.")
            print("You will get a notification when each digest is ready.")
            print("Turn it off with:  python3 schedule.py remove")
        return rc

    if action == "remove":
        remove()
        print(f"Removed the {what} job. The digest still runs when you open "
              f"the app.")
        return 0

    if action == "status":
        print(f"{what}: {'scheduled' if status() else 'not scheduled'}")
        return 0

    print(__doc__)
    return 64


if __name__ == "__main__":
    sys.exit(main())
