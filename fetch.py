#!/usr/bin/env python3
"""
Fetch tonight's arXiv announcement for the sections in config.toml, score every
paper against the author and topic lists, and write a compact briefing.

Standard library only — no pip install needed.

Outputs (under runs/<listing-date>/):
    items.json       every paper in the announcement, parsed
    candidates.json  scored + ranked papers above min_score
    brief.md         the briefing handed to Claude

Usage:
    python3 fetch.py                 # today's announcement
    python3 fetch.py --force         # re-fetch even if already digested
    python3 fetch.py --cache FILE    # parse a saved feed instead of the network
"""

import argparse
import importlib
import json
import os
import re
import ssl
import sys
import tomllib
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

HOME = Path(__file__).resolve().parent
NS = {"dc": "http://purl.org/dc/elements/1.1/",
      "arxiv": "http://arxiv.org/schemas/atom"}
USER_AGENT = "arxiv-digest/1.0 (personal nightly reading list)"

PARTICLES = {"van", "von", "de", "del", "della", "der", "den", "di", "da",
             "dos", "du", "la", "le", "ter", "ten", "bin", "al", "st"}
SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}


# --------------------------------------------------------------------------
# name normalisation
# --------------------------------------------------------------------------

_LATEX_ACCENT = re.compile(r"\\([`'\"^~=.uvHcijkrbdt])\s*\{?([A-Za-z])\}?")
_LATEX_LIGATURE = {r"\aa": "a", r"\AA": "A", r"\ss": "ss", r"\o": "o",
                   r"\O": "O", r"\l": "l", r"\L": "L", r"\ae": "ae",
                   r"\AE": "AE", r"\oe": "oe", r"\OE": "OE"}


_COMBINING = {"'": "́", "`": "̀", '"': "̈", "^": "̂",
              "~": "̃", "=": "̄", ".": "̇", "u": "̆",
              "v": "̌", "H": "̋", "c": "̧", "k": "̨",
              "r": "̊", "d": "̣", "b": "̱"}
_UNICODE_LIGATURE = {r"\aa": "å", r"\AA": "Å", r"\ss": "ß", r"\o": "ø",
                     r"\O": "Ø", r"\l": "ł", r"\L": "Ł", r"\ae": "æ",
                     r"\AE": "Æ", r"\oe": "œ", r"\OE": "Œ"}


def pretty_name(text: str) -> str:
    """Render LaTeX-escaped names for display: Antol\\'in -> Antolín."""
    for k, v in _UNICODE_LIGATURE.items():
        text = text.replace(k, v)

    def sub(m):
        mark = _COMBINING.get(m.group(1))
        if not mark:
            return m.group(2)
        return unicodedata.normalize("NFC", m.group(2) + mark)

    text = _LATEX_ACCENT.sub(sub, text)
    return text.replace("{", "").replace("}", "").replace("\\", "").strip()


def deaccent(text: str) -> str:
    """Turn LaTeX-escaped and unicode-accented names into plain ASCII."""
    for k, v in _LATEX_LIGATURE.items():
        text = text.replace(k, v)
    text = _LATEX_ACCENT.sub(r"\2", text)
    text = text.replace("{", "").replace("}", "").replace("\\", "")
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ascii", "ignore").decode("ascii")


def name_key(name: str):
    """
    Reduce a personal name to (lastname, first-initial) so that
    'Ada Lovelace', 'A. Lovelace' and 'Lovelace, Ada' all agree.
    Returns None if the name has no usable structure.
    """
    name = deaccent(name).strip()
    if not name:
        return None

    if "," in name:
        last_part, _, first_part = name.partition(",")
    else:
        tokens = [t for t in re.split(r"\s+", name) if t]
        tokens = [t for t in tokens if t.lower().strip(".") not in SUFFIXES]
        if len(tokens) < 2:
            return None
        # absorb nobiliary particles: "Felix von Oppen" -> last = "von oppen"
        cut = len(tokens) - 1
        while cut > 1 and tokens[cut - 1].lower() in PARTICLES:
            cut -= 1
        last_part = " ".join(tokens[cut:])
        first_part = " ".join(tokens[:cut])

    last = re.sub(r"[^a-z\- ]", "", last_part.lower()).strip()
    first = re.sub(r"[^a-z]", "", first_part.lower())
    if not last or not first:
        return None
    return (last, first[0])


# --------------------------------------------------------------------------
# feed fetching / parsing
# --------------------------------------------------------------------------

_SSL_CONTEXT = None


def ssl_context():
    """
    A verifying context that also trusts a shipped CA list, cached per run.

    On Windows, Python verifies against a snapshot of the Windows certificate
    store, and Windows only fetches a root the first time its *own* TLS stack
    meets it. arxiv.org chains to Certainly Root R1, so on a machine that has
    never been there, every fetch fails with CERTIFICATE_VERIFY_FAILED and
    "arXiv unreachable" - then starts working later once something else visits
    the site and the root gets cached, which makes it look intermittent.
    Reproduced on both a conda and a stock python.org install here.

    Adding a real CA bundle makes it deterministic, and costs no dependency:
    conda ships certifi, and a python.org install has pip's copy of it. This
    only ever adds roots to the system set, so verification never gets weaker,
    and if no bundle is found the behaviour is exactly what it was.
    """
    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        _SSL_CONTEXT = ssl.create_default_context()
        for module in ("certifi", "pip._vendor.certifi"):
            try:
                where = importlib.import_module(module).where()
                if where and os.path.exists(where):
                    _SSL_CONTEXT.load_verify_locations(cafile=where)
                    break
            except Exception:
                continue
    return _SSL_CONTEXT


def fetch_feed(archive: str, timeout: int = 60) -> str:
    url = f"https://rss.arxiv.org/rss/{archive}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout,
                                context=ssl_context()) as resp:
        return resp.read().decode("utf-8", "replace")


def parse_feed(xml_text: str):
    """Return (listing_date, [item dicts])."""
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("feed has no <channel> — arXiv may be down")

    listing_date = None
    chan_date = channel.findtext("pubDate")
    if chan_date:
        try:
            listing_date = parsedate_to_datetime(chan_date).date().isoformat()
        except (TypeError, ValueError):
            listing_date = None

    items = []
    for node in channel.findall("item"):
        title = " ".join((node.findtext("title") or "").split())
        link = (node.findtext("link") or "").strip()
        desc = node.findtext("description") or ""
        creators = node.findtext("dc:creator", namespaces=NS) or ""
        cats = [c.text.strip() for c in node.findall("category") if c.text]

        # arXiv uses four announce types: new, cross, replace, replace-cross.
        # Anything containing "replace" is an update to an existing paper.
        announce = "new"
        m = re.search(r"Announce Type:\s*(\S+)", desc)
        if m:
            announce = m.group(1).lower()
        is_replacement = "replace" in announce

        abstract = desc
        m = re.search(r"Abstract:\s*(.*)", desc, re.S)
        if m:
            abstract = m.group(1)
        abstract = " ".join(abstract.split())

        arxiv_id, version = "", ""
        m = re.search(r"arXiv:(\d{4}\.\d{4,5})(v\d+)?", desc)
        if m:
            arxiv_id, version = m.group(1), m.group(2) or ""
        elif link:
            m = re.search(r"(\d{4}\.\d{4,5})", link)
            if m:
                arxiv_id = m.group(1)

        authors = [a.strip() for a in re.split(r",(?![^(]*\))", creators)
                   if a.strip()]

        items.append({
            "id": arxiv_id,
            "version": version,
            "announce": announce,
            "is_replacement": is_replacement,
            "title": title,
            "authors": authors,
            "abstract": abstract,
            "categories": cats,
            "primary": cats[0] if cats else "",
            "link": link or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""),
        })

    if not listing_date:
        listing_date = datetime.now(timezone.utc).date().isoformat()
    return listing_date, items


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def compile_topics(topics):
    out = []
    for t in topics:
        pats = []
        for kw in t.get("keywords", []):
            try:
                pats.append((kw, re.compile(kw, re.I)))
            except re.error as exc:
                print(f"  ! bad regex in topic '{t['name']}': {kw} ({exc})",
                      file=sys.stderr)
        out.append({"name": t["name"],
                    "weight": float(t.get("weight", 1.0)),
                    "patterns": pats})
    return out


def followed_archives(cfg):
    """The archives being read, e.g. {"cond-mat"} or {"quant-ph", "hep-th"}."""
    return {str(f).split(".")[0] for f in cfg.get("feeds", [])}


def score_item(item, author_index, topics, cfg, archives=None):
    """Attach score + explanation fields to item; return the score."""
    score = 0.0
    reasons = []
    signals = []

    # --- authors -----------------------------------------------------------
    matched = []
    n = len(item["authors"])
    for pos, raw in enumerate(item["authors"]):
        key = name_key(raw)
        if key and key in author_index:
            first_or_last = pos == 0 or pos == n - 1
            matched.append({"name": raw,
                            "listed_as": author_index[key],
                            "position": pos + 1,
                            "prominent_position": first_or_last})
    seen = set()
    unique = []
    for m in matched:
        if m["listed_as"] not in seen:
            seen.add(m["listed_as"])
            unique.append(m)
    matched = unique

    if matched:
        pts = cfg["author_points"] * len(matched)
        pts += sum(cfg["author_points_first_last"] for m in matched
                   if m["prominent_position"])
        score += pts
        who = ", ".join(m["listed_as"] for m in matched)
        reasons.append(f"authors +{pts:.1f} ({who})")
        signals.append(f"{len(matched)} tracked author(s): {who}")

    # --- topics ------------------------------------------------------------
    title = item["title"]
    abstract = item["abstract"]
    topic_hits = []
    for t in topics:
        if t["weight"] <= 0:
            continue
        in_title, in_abs = [], []
        for kw, rx in t["patterns"]:
            if rx.search(title):
                in_title.append(kw)
            elif rx.search(abstract):
                in_abs.append(kw)
        total = len(in_title) + len(in_abs)
        if not total:
            continue
        w = t["weight"]
        pts = w + 0.25 * w * (total - 1)
        if in_title:
            pts += w * (cfg["title_multiplier"] - 1.0)
        pts = min(pts, w * 3.0)
        score += pts
        topic_hits.append({"topic": t["name"], "points": round(pts, 2),
                           "title_hits": in_title, "abstract_hits": in_abs})
        reasons.append(f"{t['name']} +{pts:.1f}"
                       + (" [title]" if in_title else ""))

    # --- structural bonuses ------------------------------------------------
    if any(h["topic"] == "reviews" for h in topic_hits):
        score += cfg["review_bonus"]
        reasons.append(f"review +{cfg['review_bonus']:.1f}")
        signals.append("reads like a review / colloquium / lecture notes")

    # "Outside" means outside the archives you actually follow. Hardcoding
    # cond-mat here would hand a quant-ph reader a cross-list bonus on nearly
    # every paper, which is the opposite of the signal intended.
    archives = archives or {"cond-mat"}
    outside = [c for c in item["categories"]
               if c.split(".")[0] not in archives]
    if outside:
        pts = cfg["cross_list_bonus"] * len(outside)
        score += pts
        reasons.append(f"cross-listed +{pts:.1f} ({', '.join(outside)})")
        signals.append("spans " + ", ".join(item["categories"]))

    if item["announce"] == "cross":
        signals.append(f"cross-listed in from {item['primary']}")
    if item["is_replacement"]:
        signals.append(f"replacement {item['version']} of an existing paper")
    if item["announce"] == "replace-cross":
        signals.append("existing paper newly cross-listed into your sections")

    item["score"] = round(score, 2)
    item["matched_authors"] = matched
    item["topic_hits"] = topic_hits
    item["why"] = reasons
    item["influence_signals"] = signals
    return score


# --------------------------------------------------------------------------
# briefing
# --------------------------------------------------------------------------

def one_line_authors(item, limit=6):
    a = [pretty_name(x) for x in item["authors"]]
    if len(a) <= limit:
        return ", ".join(a)
    return ", ".join(a[:limit]) + f", +{len(a) - limit} more"


def write_brief(path, listing_date, items, candidates, cfg, prefs, reader,
                sections=None, topped_up=0):
    counts = {}
    for it in items:
        counts[it["announce"]] = counts.get(it["announce"], 0) + 1
    tally = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))

    L = []
    L.append(f"# arXiv {', '.join(sections or ['(no sections set)'])} — "
             f"listing for {listing_date}")
    L.append("")
    L.append(f"Announcement contains {len(items)} papers ({tally}).")
    L.append(f"{len(candidates)} cleared the relevance filter "
             f"(min_score={cfg['min_score']}).")
    L.append("")
    if topped_up:
        L.append(f"NOTE: the author and topic lists are barely configured, so "
                 f"only {len(candidates) - topped_up} papers actually matched. "
                 f"The remaining {topped_up} were added just to give you "
                 f"something to read — judge them purely on merit, and say in "
                 f"one line that the digest will sharpen once the reader "
                 f"profile is set up.")
        L.append("")
    L.append("Scoring is a keyword/author prefilter only — it is deliberately "
             "generous and makes no judgement about quality. Use the abstracts "
             "below to decide what actually matters.")
    L.append("")

    if reader:
        L.append("## Who this is for")
        L.append("")
        L.append(reader.strip())
        L.append("")

    if prefs:
        L.append("## Learned preferences (from papers actually downloaded)")
        L.append("")
        L.append(prefs.strip())
        L.append("")

    n = 0
    for label, group in (
        ("Shortlisted new + cross-listed papers (full abstracts)",
         [c for c in candidates if not c["is_replacement"]]),
        ("Shortlisted replacements — existing papers updated tonight",
         [c for c in candidates if c["is_replacement"]]),
    ):
        if not group:
            continue
        L.append(f"## {label}")
        L.append("")
        for it in group:
            n += 1
            L.extend(render_candidate(n, it))
    _append_titles(L, items)
    path.write_text("\n".join(L), encoding="utf-8")


def render_candidate(i, it):
    L = [f"### [{i}] {it['title']}"]
    L.append(f"- **id**: arXiv:{it['id']}{it['version']}  "
             f"({it['announce'].upper()})   **link**: {it['link']}")
    L.append(f"- **authors**: {one_line_authors(it, 12)}")
    L.append(f"- **categories**: {', '.join(it['categories'])}")
    L.append(f"- **prefilter score**: {it['score']}  —  {'; '.join(it['why'])}")
    if it["influence_signals"]:
        L.append(f"- **signals**: {'; '.join(it['influence_signals'])}")
    L.append(f"- **abstract**: {it['abstract']}")
    L.append("")
    return L


def _append_titles(L, items):
    fresh = [it for it in items if not it["is_replacement"]]
    L.append(f"## All {len(fresh)} new + cross-listed titles "
             f"(for trend analysis only — no abstracts)")
    L.append("")
    for it in fresh:
        L.append(f"- [{it['primary']}] {it['title']} — "
                 f"{one_line_authors(it, 3)}")
    L.append("")

    reps = [it for it in items if it["is_replacement"]]
    if reps:
        L.append(f"## {len(reps)} replacements in this announcement "
                 f"(titles only; shortlisted ones appear above)")
        L.append("")
        for it in reps:
            L.append(f"- arXiv:{it['id']}{it['version']} [{it['primary']}] "
                     f"{it['title']} — {one_line_authors(it, 3)}")
        L.append("")


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="re-run even if this listing was already digested")
    ap.add_argument("--cache", help="parse a saved feed XML file instead of fetching")
    ap.add_argument("--config", default=str(HOME / "config.toml"))
    args = ap.parse_args()

    with open(args.config, "rb") as fh:
        conf = tomllib.load(fh)
    cfg = conf.get("settings", {})
    cfg.setdefault("max_candidates", 32)
    cfg.setdefault("min_score", 2.0)
    cfg.setdefault("include_replacements", True)
    cfg.setdefault("replacement_min_score", 5.0)
    cfg.setdefault("author_points", 5.0)
    cfg.setdefault("author_points_first_last", 2.0)
    cfg.setdefault("title_multiplier", 2.0)
    cfg.setdefault("review_bonus", 3.0)
    cfg.setdefault("cross_list_bonus", 0.5)

    author_index = {}
    skipped = []
    for raw in conf.get("authors", {}).get("names", []):
        key = name_key(raw)
        if key:
            author_index.setdefault(key, raw)
        else:
            skipped.append(raw)
    if skipped:
        print(f"  ! could not parse {len(skipped)} author name(s): "
              f"{', '.join(skipped[:5])}", file=sys.stderr)

    topics = compile_topics(conf.get("topics", []))

    # --- get the announcement ---------------------------------------------
    if args.cache:
        xml_text = Path(args.cache).read_text(encoding="utf-8")
        listing_date, items = parse_feed(xml_text)
    else:
        feeds = [f for f in cfg.get("feeds", []) if str(f).strip()]
        if not feeds:
            print("No arXiv sections are configured, so there is nothing to "
                  "fetch.\nOpen the app and choose sections in setup step 3, "
                  "or set e.g.\n    feeds = [\"quant-ph\"]\nin config.toml.",
                  file=sys.stderr)
            return 4
        merged, listing_date, seen_ids = [], None, set()
        for archive in feeds:
            try:
                xml_text = fetch_feed(archive)
            except (urllib.error.URLError, TimeoutError) as exc:
                print(f"  ! could not fetch {archive}: {exc}", file=sys.stderr)
                continue
            d, its = parse_feed(xml_text)
            listing_date = listing_date or d
            for it in its:
                tag = (it["id"], it["announce"])
                if tag not in seen_ids:
                    seen_ids.add(tag)
                    merged.append(it)
        items = merged
        if not items:
            print("No papers retrieved — arXiv unreachable or feed empty.",
                  file=sys.stderr)
            return 2

    run_dir = HOME / "runs" / listing_date
    digest_file = HOME / "digests" / f"{listing_date}.md"
    if digest_file.exists() and not args.force:
        print(f"ALREADY_DIGESTED {listing_date} -> {digest_file}")
        print("Use --force to redo it.")
        return 3

    # --- score -------------------------------------------------------------
    archives = followed_archives(cfg)
    for it in items:
        score_item(it, author_index, topics, cfg, archives)

    # Rank new/cross and replacements in separate pools with separate slot
    # budgets, so a high-scoring v3 update can never displace tonight's new work.
    fresh_pool, repl_pool = [], []
    for it in items:
        if it["is_replacement"]:
            if cfg["include_replacements"] and it["score"] >= cfg["replacement_min_score"]:
                repl_pool.append(it)
        elif it["score"] >= cfg["min_score"]:
            fresh_pool.append(it)

    by_score = lambda x: (-x["score"], x["announce"] != "new", x["id"])
    fresh_pool.sort(key=by_score)
    repl_pool.sort(key=by_score)
    candidates = (fresh_pool[:int(cfg["max_candidates"])]
                  + repl_pool[:int(cfg.get("max_replacements", 6))])

    # A config with no authors and few topics — which is what a fresh install
    # has until setup step 2 runs — would clear the score floor on almost
    # nothing and produce an empty digest. Top the shortlist up with the
    # best-scoring remaining new papers so day one is still useful, and say so
    # in the briefing rather than pretending these were matches.
    topped_up = 0
    floor = int(cfg.get("min_shortlist", 12))
    if len(candidates) < floor:
        have = {c["id"] for c in candidates}
        extra = [it for it in items
                 if not it["is_replacement"] and it["id"] not in have]
        extra.sort(key=by_score)
        add = extra[:floor - len(candidates)]
        candidates += add
        topped_up = len(add)

    prefs_file = HOME / "preferences.md"
    prefs = prefs_file.read_text(encoding="utf-8") if prefs_file.exists() else ""
    reader = (conf.get("reader", {}) or {}).get("description", "").strip()

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "items.json").write_text(
        json.dumps({"listing_date": listing_date, "items": items},
                   indent=1, ensure_ascii=False), encoding="utf-8")
    (run_dir / "candidates.json").write_text(
        json.dumps({"listing_date": listing_date,
                    "shown": [c["id"] for c in candidates],
                    "candidates": candidates}, indent=1, ensure_ascii=False),
        encoding="utf-8")
    write_brief(run_dir / "brief.md", listing_date, items, candidates, cfg,
                prefs, reader, cfg.get("feeds", []), topped_up)

    n_auth = sum(1 for c in candidates if c["matched_authors"])
    print(f"LISTING_DATE {listing_date}")
    print(f"TOTAL {len(items)}  SHORTLISTED {len(candidates)}  "
          f"WITH_TRACKED_AUTHOR {n_auth}")
    print(f"BRIEF {run_dir / 'brief.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
