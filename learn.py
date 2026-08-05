#!/usr/bin/env python3
"""
Build a statistics report comparing what the digest surfaced against what was
actually downloaded. Written to stdout; learn.sh feeds it to Claude, which turns
it into preferences.md.

Usage:  python3 learn.py
"""

import json
import tomllib
from collections import Counter
from pathlib import Path

import fetch  # reuse name_key() so author matching agrees exactly

HOME = Path(__file__).resolve().parent


def main():
    picks_file = HOME / "picks.jsonl"
    if not picks_file.exists() or not picks_file.read_text().strip():
        print("NO_PICKS")
        print("No picks recorded yet, so there is nothing to learn from.")
        print("Tick the papers you download in library.md, then run ./learn.sh.")
        return

    picks = [json.loads(l) for l in
             picks_file.read_text(encoding="utf-8").splitlines() if l.strip()]

    with open(HOME / "config.toml", "rb") as fh:
        conf = tomllib.load(fh)
    in_config = {}
    for raw in conf.get("authors", {}).get("names", []):
        k = fetch.name_key(raw)
        if k:
            in_config[k] = raw
    topic_weights = {t["name"]: t.get("weight", 1.0)
                     for t in conf.get("topics", [])}

    # ---- what was surfaced across all stored nights ------------------------
    shown_topics = Counter()
    shown_cats = Counter()
    nights = 0
    shown_total = 0
    for cand_file in sorted(HOME.glob("runs/*/candidates.json")):
        nights += 1
        cands = json.loads(cand_file.read_text(encoding="utf-8"))["candidates"]
        shown_total += len(cands)
        for c in cands:
            shown_cats[c["primary"]] += 1
            for h in c.get("topic_hits", []):
                shown_topics[h["topic"]] += 1

    picked_topics = Counter()
    picked_cats = Counter()
    for p in picks:
        for t in p.get("topics", []):
            picked_topics[t] += 1
        picked_cats[p["primary"]] += 1

    print("# Digest performance report")
    print()
    print(f"- Picks recorded: **{len(picks)}** across "
          f"{len({p['listing_date'] for p in picks})} listing(s)")
    print(f"- Nights of stored digest data: {nights} "
          f"({shown_total} papers shortlisted in total)")
    print()

    # ---- precision of the recommendation ----------------------------------
    surfaced = Counter(p.get("surfaced_as", "?") for p in picks)
    print("## Where the picks came from")
    print()
    print("How well the digest's own ranking predicted what got downloaded:")
    print()
    labels = {"top5": "made Tonight's five",
              "also": "was in 'Also worth a look'",
              "shortlisted": "cleared the prefilter but was NOT recommended",
              "not_surfaced": "was never shortlisted at all — a miss"}
    for key, label in labels.items():
        n = surfaced.get(key, 0)
        if n or key in ("top5", "not_surfaced"):
            pct = 100.0 * n / len(picks)
            print(f"- {label}: **{n}** ({pct:.0f}%)")
    print()
    if surfaced.get("not_surfaced"):
        print("Misses are the most important signal here — those papers were "
              "filtered out entirely, so whatever they have in common is not yet "
              "represented in the config.")
        print()
        for p in picks:
            if p.get("surfaced_as") == "not_surfaced":
                print(f"  - MISSED: {p['title']} ({p['primary']}) "
                      f"— score was {p.get('score')}")
        print()

    # ---- topics ------------------------------------------------------------
    print("## Topic hit rates")
    print()
    print("`picked / shown` — a topic shown constantly but never picked is "
          "over-weighted; a topic picked nearly every time it appears is "
          "under-weighted.")
    print()
    print("| topic | weight | shown | picked | rate |")
    print("|---|---|---|---|---|")
    for topic in sorted(set(shown_topics) | set(picked_topics),
                        key=lambda t: -picked_topics[t]):
        shown = shown_topics.get(topic, 0)
        got = picked_topics.get(topic, 0)
        rate = f"{100.0 * got / shown:.0f}%" if shown else "n/a"
        print(f"| {topic} | {topic_weights.get(topic, '?')} | {shown} | "
              f"{got} | {rate} |")
    print()

    dead = [t for t in shown_topics
            if shown_topics[t] >= 8 and not picked_topics.get(t)]
    if dead:
        print(f"Shown at least 8 times and never picked: "
              f"**{', '.join(sorted(dead))}**")
        print()

    # ---- authors -----------------------------------------------------------
    print("## Authors in the picked papers")
    print()
    counts = Counter()
    display = {}
    for p in picks:
        for a in p["authors"]:
            k = fetch.name_key(a)
            if k:
                counts[k] += 1
                display.setdefault(k, a)
    tracked = [(display[k], n) for k, n in counts.items() if k in in_config]
    untracked = [(display[k], n) for k, n in counts.items()
                 if k not in in_config and n >= 2]
    if tracked:
        print("Already on the list: " + ", ".join(
            f"{a} ({n})" for a, n in sorted(tracked, key=lambda x: -x[1])))
    else:
        print("None of the picked papers were by anyone on the author list.")
    print()
    if untracked:
        print("**Not on the list, but appeared in 2+ picked papers** — "
              "candidates to add:")
        for a, n in sorted(untracked, key=lambda x: -x[1]):
            print(f"  - {a} — {n} picked papers")
    else:
        print("No off-list author appeared in 2 or more picked papers yet.")
    print()

    never = [name for k, name in in_config.items() if k not in counts]
    print(f"{len(never)} of {len(in_config)} listed authors have never appeared "
          f"in a picked paper. (Expected while the sample is small — only prune "
          f"after many nights.)")
    print()

    # ---- categories --------------------------------------------------------
    print("## Primary categories")
    print()
    print("| category | shown | picked |")
    print("|---|---|---|")
    for cat in sorted(set(shown_cats) | set(picked_cats),
                      key=lambda c: -picked_cats[c]):
        print(f"| {cat} | {shown_cats.get(cat, 0)} | {picked_cats.get(cat, 0)} |")
    print()

    # ---- scores ------------------------------------------------------------
    scores = sorted(p["score"] for p in picks if isinstance(p.get("score"),
                                                            (int, float)))
    if scores:
        mid = scores[len(scores) // 2]
        print(f"## Prefilter scores of picked papers")
        print()
        print(f"- lowest {scores[0]}, median {mid}, highest {scores[-1]}")
        print(f"- current `min_score` is "
              f"{conf.get('settings', {}).get('min_score')}")
        if scores[0] < conf.get("settings", {}).get("min_score", 2.0) + 1:
            print("- at least one pick scored near the cutoff, so raising "
                  "`min_score` would risk losing real hits")
        print()

    # ---- announce type -----------------------------------------------------
    ann = Counter(p.get("announce", "?") for p in picks)
    print("## Announce types picked")
    print()
    print(", ".join(f"{k}: {v}" for k, v in ann.most_common()))
    print()

    print("## Titles of every picked paper")
    print()
    for p in sorted(picks, key=lambda x: x["listing_date"], reverse=True):
        print(f"- [{p['listing_date']}] **{p['title']}** — "
              f"{', '.join(p['authors'][:3])}"
              f"{' et al.' if len(p['authors']) > 3 else ''} "
              f"({p['primary']}; topics: {', '.join(p.get('topics')) or 'none'})")
    print()


if __name__ == "__main__":
    main()
