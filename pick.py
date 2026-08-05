#!/usr/bin/env python3
"""
Record which papers you actually downloaded. This is the training signal.

    python3 pick.py --sync                 read the ticked boxes in library.md
    python3 pick.py 2607.12345 2607.6789   record specific papers directly
    python3 pick.py --unpick 2607.12345    undo one
    python3 pick.py --list                 show what has been recorded so far

Picks accumulate in picks.jsonl. Recording the same paper twice is harmless.
The reader app (app.py) calls the functions here directly, so ticks made in the
browser, in library.md, and on the command line all end up in the same place.
"""

import argparse
import json
import re
from datetime import date
from pathlib import Path

HOME = Path(__file__).resolve().parent
PICKS = HOME / "picks.jsonl"
LIBRARY = HOME / "library.md"

ID_RE = r"\d{4}\.\d{4,5}"


def normalise(raw):
    """'arXiv:2607.12345v2' -> '2607.12345'"""
    m = re.search(ID_RE, str(raw))
    return m.group(0) if m else None


def marked_ids(digest_text, tag):
    """Read the '<!-- TOP5: ... -->' / '<!-- ALSO: ... -->' footer of a digest."""
    m = re.search(rf"<!--\s*{tag}:\s*([^>]*?)-->", digest_text)
    return re.findall(ID_RE, m.group(1)) if m else []


def load_runs():
    """Map arxiv id -> (listing_date, item, how_it_was_surfaced)."""
    index = {}
    for run in sorted(HOME.glob("runs/*/items.json")):
        listing = run.parent.name
        data = json.loads(run.read_text(encoding="utf-8"))
        cand_file = run.parent / "candidates.json"
        shortlisted = set()
        if cand_file.exists():
            shortlisted = {c["id"] for c in json.loads(
                cand_file.read_text(encoding="utf-8"))["candidates"]}
        digest = HOME / "digests" / f"{listing}.md"
        top5, also = set(), set()
        if digest.exists():
            text = digest.read_text(encoding="utf-8")
            top5.update(marked_ids(text, "TOP5"))
            also.update(marked_ids(text, "ALSO"))
        for it in data["items"]:
            if not it["id"]:
                continue
            tag = ("top5" if it["id"] in top5 else
                   "also" if it["id"] in also else
                   "shortlisted" if it["id"] in shortlisted else "not_surfaced")
            index[it["id"]] = (listing, it, tag)
    return index


def existing_picks():
    """Map arxiv id -> pick record."""
    if not PICKS.exists():
        return {}
    out = {}
    for line in PICKS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rec = json.loads(line)
                out[rec["id"]] = rec
            except json.JSONDecodeError:
                continue
    return out


def picked_ids():
    return set(existing_picks())


def ticked_in_library():
    if not LIBRARY.exists():
        return []
    ids = []
    for line in LIBRARY.read_text(encoding="utf-8").splitlines():
        if re.match(r"\s*- \[[xX]\]", line):
            m = re.search(rf"`({ID_RE})`", line) or re.search(f"({ID_RE})", line)
            if m:
                ids.append(m.group(1))
    return ids


def set_library_ticks(marks):
    """marks: {arxiv_id: True/False} — reflect picks back into library.md."""
    if not LIBRARY.exists() or not marks:
        return
    lines = LIBRARY.read_text(encoding="utf-8").split("\n")
    for i, line in enumerate(lines):
        m = re.search(rf"`({ID_RE})`", line)
        if not m or m.group(1) not in marks:
            continue
        want = marks[m.group(1)]
        lines[i] = (re.sub(r"- \[ \]", "- [x]", line, count=1) if want
                    else re.sub(r"- \[[xX]\]", "- [ ]", line, count=1))
    LIBRARY.write_text("\n".join(lines), encoding="utf-8")


def record_ids(ids, index=None):
    """Append picks for these arXiv ids. Returns (added, skipped, unknown)."""
    ids = [n for n in (normalise(i) for i in ids) if n]
    if not ids:
        return [], [], []
    have = existing_picks()
    index = index if index is not None else load_runs()
    added, skipped, unknown = [], [], []
    with PICKS.open("a", encoding="utf-8") as fh:
        for pid in dict.fromkeys(ids):
            if pid in have:
                skipped.append(pid)
                continue
            if pid not in index:
                unknown.append(pid)
                continue
            listing, it, tag = index[pid]
            fh.write(json.dumps({
                "id": pid,
                "listing_date": listing,
                "recorded_on": date.today().isoformat(),
                "title": it["title"],
                "authors": it["authors"],
                "primary": it["primary"],
                "categories": it["categories"],
                "announce": it["announce"],
                "topics": [h["topic"] for h in it.get("topic_hits", [])],
                "matched_authors": [m["listed_as"]
                                    for m in it.get("matched_authors", [])],
                "score": it.get("score"),
                "surfaced_as": tag,
            }, ensure_ascii=False) + "\n")
            added.append(pid)
    set_library_ticks({p: True for p in added + skipped})
    return added, skipped, unknown


def unrecord_ids(ids):
    """Remove picks for these ids. Returns the ids actually removed."""
    ids = {n for n in (normalise(i) for i in ids) if n}
    if not ids or not PICKS.exists():
        return []
    kept, removed = [], []
    for line in PICKS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("id") in ids:
            removed.append(rec["id"])
        else:
            kept.append(line)
    PICKS.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    set_library_ticks({p: False for p in removed})
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*", help="arXiv ids, e.g. 2607.12345")
    ap.add_argument("--sync", action="store_true",
                    help="record every ticked box in library.md")
    ap.add_argument("--unpick", nargs="+", metavar="ID", default=[],
                    help="remove these ids from the pick history")
    ap.add_argument("--list", action="store_true", help="show recorded picks")
    args = ap.parse_args()

    if args.list:
        have = existing_picks()
        if not have:
            print("No picks recorded yet.")
            return
        print(f"{len(have)} picks recorded:\n")
        for rec in sorted(have.values(), key=lambda r: r["listing_date"],
                          reverse=True):
            print(f"  {rec['listing_date']}  [{rec.get('surfaced_as','?'):11s}] "
                  f"{rec['title'][:64]}\n      topics: "
                  f"{','.join(rec.get('topics', [])) or '-'}")
        return

    if args.unpick:
        removed = unrecord_ids(args.unpick)
        print(f"Removed {len(removed)} pick(s)."
              if removed else "Nothing matched.")
        return

    wanted = list(args.ids) + (ticked_in_library() if args.sync else [])
    if not wanted:
        print("Nothing to record. Pass arXiv ids, or tick boxes in library.md "
              "and rerun with --sync.")
        return

    added, skipped, unknown = record_ids(wanted)
    print(f"Recorded {len(added)} new pick(s); {len(skipped)} already known.")
    if unknown:
        print("Not found in any stored run (too old, or wrong id): "
              + ", ".join(unknown))
    total = len(picked_ids())
    if total < 10:
        print(f"{total} picks so far — around 10-15 gives ./learn.sh "
              "enough to work with.")


if __name__ == "__main__":
    main()
