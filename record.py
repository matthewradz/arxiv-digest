#!/usr/bin/env python3
"""
Append the papers surfaced in a digest to library.md, as a tickable checklist.

Tick the box next to anything you actually download, then run ./learn.sh —
the ticks are the training signal for what you find interesting.

Usage:  python3 record.py --date 2026-08-03
"""

import argparse
import json
import re
from pathlib import Path

import fetch  # for pretty_name()
import pick   # for marked_ids()

HOME = Path(__file__).resolve().parent
LIBRARY = HOME / "library.md"

HEADER = """# Paper library

Every paper the nightly digest surfaced, newest first.

**Tick the box next to any paper you download.** Then run `./learn.sh` and the
digest will use your picks to get better at guessing what you want. Ticks are the
only training signal, so they matter more than they look.

`[T]` = the digest put it in tonight's five.  `[A]` = "also worth a look".
`[S]` = it cleared the prefilter but the digest did not recommend it.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    args = ap.parse_args()

    digest = HOME / "digests" / f"{args.date}.md"
    items_file = HOME / "runs" / args.date / "items.json"
    cand_file = HOME / "runs" / args.date / "candidates.json"
    if not digest.exists() or not items_file.exists():
        raise SystemExit(f"missing digest or run data for {args.date}")

    text = digest.read_text(encoding="utf-8")
    items = {it["id"]: it for it in json.loads(
        items_file.read_text(encoding="utf-8"))["items"] if it["id"]}
    shortlisted = [c["id"] for c in json.loads(
        cand_file.read_text(encoding="utf-8"))["candidates"]]

    top5 = pick.marked_ids(text, "TOP5")
    also = pick.marked_ids(text, "ALSO")

    # Guard against the digest citing a paper that was not in the announcement.
    # Only ids used as the digest's own references count — ids appearing inside
    # an abstract (papers citing other papers) are legitimate and ignored.
    referenced = set(top5) | set(also) | set(
        re.findall(r"`arXiv:(\d{4}\.\d{4,5})", text))
    unverified = sorted(referenced - set(items))
    if unverified:
        print("WARNING: the digest references papers that are not in this "
              "announcement: " + ", ".join(unverified))
        print("         Check them against arxiv.org before trusting them.")
    # Fall back to ids appearing in the body if the comments are missing.
    if not top5:
        top5 = list(dict.fromkeys(re.findall(r"arXiv:(\d{4}\.\d{4,5})", text)))[:5]

    ordered = []
    for tag, ids in (("T", top5), ("A", also), ("S", shortlisted)):
        for pid in ids:
            if pid not in {o[1] for o in ordered}:
                ordered.append((tag, pid))

    lines = [f"## {args.date}", ""]
    for tag, pid in ordered:
        it = items.get(pid)
        if not it:
            lines.append(f"- [ ] `[{tag}]` arXiv:{pid} "
                         f"— https://arxiv.org/abs/{pid}")
            continue
        authors = [fetch.pretty_name(a) for a in it["authors"]]
        who = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        ver = it["version"] if it["is_replacement"] else ""
        lines.append(
            f"- [ ] `[{tag}]` **{it['title']}** — {who} "
            f"· {it['primary']} · [abs](https://arxiv.org/abs/{pid}{ver}) "
            f"· [pdf](https://arxiv.org/pdf/{pid}{ver}) · `{pid}`")
    lines.append("")

    block = "\n".join(lines)
    if LIBRARY.exists():
        old = LIBRARY.read_text(encoding="utf-8")
        # Replace an existing section for this date rather than duplicating it,
        # but keep any ticks already made.
        pat = re.compile(rf"^## {re.escape(args.date)}\n.*?(?=^## |\Z)",
                         re.S | re.M)
        existing = pat.search(old)
        if existing:
            ticked = set(re.findall(r"- \[[xX]\].*?`(\d{4}\.\d{4,5})`",
                                    existing.group(0)))
            if ticked:
                block = "\n".join(
                    ln.replace("- [ ]", "- [x]", 1)
                    if any(f"`{t}`" in ln for t in ticked) else ln
                    for ln in block.split("\n"))
            # lambda, not a string: titles contain LaTeX like \mathcal, which
            # re.sub would try to interpret as escape sequences.
            replacement = block.rstrip() + "\n\n"
            body = pat.sub(lambda _: replacement, old, count=1)
        else:
            head, sep, rest = old.partition("\n## ")
            body = (head.rstrip() + "\n\n" + block + (sep + rest if sep else "\n"))
    else:
        body = HEADER + "\n" + block

    LIBRARY.write_text(body, encoding="utf-8")
    print(f"library.md: {len(ordered)} papers recorded for {args.date}")


if __name__ == "__main__":
    main()
