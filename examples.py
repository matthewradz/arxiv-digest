#!/usr/bin/env python3
"""
Merge a starter config from examples/ into your config.toml.

    python3 examples.py list
    python3 examples.py add quantum-information
    python3 examples.py add condensed-matter

Appending with `cat` does not work: TOML allows [[topics]] to repeat but not a
second [authors] table, so a plain append produces a file that will not parse.
This merges properly — authors are unioned, topic groups are added unless a
group of that name already exists — and keeps config.toml.backup.
"""

import re
import shutil
import sys
import tomllib
from pathlib import Path

import configedit
import fetch  # name_key(), so de-duplication matches the digest's

HOME = Path(__file__).resolve().parent
EXAMPLES = HOME / "examples"


def available():
    return sorted(p.stem for p in EXAMPLES.glob("*.toml")) if EXAMPLES.is_dir() else []


def describe(stem):
    """First real comment line of an example, as a one-line summary."""
    for line in (EXAMPLES / f"{stem}.toml").read_text(encoding="utf-8").splitlines():
        s = line.strip().lstrip("#").strip()
        if s and not set(s) <= {"=", "-"}:
            return s
    return ""


def add(stem):
    ex = EXAMPLES / f"{stem}.toml"
    cfg = HOME / "config.toml"
    if not ex.exists():
        print(f"No example called {stem!r}. Try: {', '.join(available())}",
              file=sys.stderr)
        return 1
    if not cfg.exists():
        print("config.toml not found — run the app once first.", file=sys.stderr)
        return 1

    ex_conf = tomllib.loads(ex.read_text(encoding="utf-8"))
    text = cfg.read_text(encoding="utf-8")
    cur = tomllib.loads(text)
    shutil.copy(cfg, HOME / "config.toml.backup")

    # ---- authors: union, keeping what is already there --------------------
    current = configedit.read_authors(text)
    have_keys = {k for k in (fetch.name_key(n) for n in current) if k}
    fresh = []
    for n in ex_conf.get("authors", {}).get("names", []):
        k = fetch.name_key(n)
        if k and k not in have_keys:
            have_keys.add(k)
            fresh.append(n)
    if fresh:
        entries = [(n, None) for n in current] + \
                  [(n, f"from {stem}") for n in fresh]
        text = configedit.set_authors(text, entries)

    # ---- topics: append groups whose name is not already present ----------
    existing = set(configedit.topic_names(text))
    added_topics = [g for g in configedit.topic_blocks(
                        ex.read_text(encoding="utf-8"))
                    if (m := re.search(r'name = "([^"]+)"', g))
                    and m.group(1) not in existing]
    text = configedit.append_topics(
        text, added_topics, f"topic groups from examples/{stem}.toml")

    # ---- never write a file that will not parse ---------------------------
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        print(f"Refusing to write: the merged config would not parse ({exc}).\n"
              f"config.toml is unchanged.", file=sys.stderr)
        return 1
    cfg.write_text(text, encoding="utf-8")

    print(f"Added {len(fresh)} authors and {len(added_topics)} topic groups "
          f"from {stem}.")
    if not fresh and not added_topics:
        print("(everything in that example was already present)")
    print("Previous config saved as config.toml.backup")
    return 0


def main():
    args = sys.argv[1:]
    if not args or args[0] == "list":
        names = available()
        if not names:
            print("No examples found.")
            return 0
        print("Starter configs you can merge in:\n")
        for n in names:
            print(f"  {n}\n      {describe(n)}")
        print("\n  python3 examples.py add <name>")
        return 0
    if args[0] == "add" and len(args) > 1:
        return add(args[1])
    print(__doc__)
    return 64


if __name__ == "__main__":
    sys.exit(main())
