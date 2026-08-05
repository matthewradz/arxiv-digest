#!/usr/bin/env python3
"""
Build an author list and topic list from a researcher's actual publication record.

    python3 profile.py "Ada Lovelace"
    python3 profile.py "Ada Lovelace" --apply      # write it into config.toml
    python3 profile.py --list "Ada Lovelace"       # just show the matches

Instead of guessing who and what to track, this reads the person's own corpus and
takes the frequent coauthors and the recurring subject areas straight from it.

Data comes from OpenAlex, which is free, needs no key, and indexes essentially
all of the physics literature. Google Scholar has no API and blocks automated
access, so a Scholar URL is accepted only as a convenience — the corpus behind it
is still fetched from OpenAlex by name.
"""

import argparse
import html
import json
import re
import sys
import tomllib
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

import fetch  # name_key / pretty_name, so matching agrees with the digest

HOME = Path(__file__).resolve().parent
API = "https://api.openalex.org"
# OpenAlex asks for a contact address so they can warn you before rate-limiting.
MAILTO = "arxiv-digest@example.com"
UA = "arxiv-digest/1.0 (nightly reading list; mailto:%s)" % MAILTO


def get(path, **params):
    params["mailto"] = MAILTO
    url = f"{API}/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))


def scholar_hint(text):
    """A Scholar URL carries no name, so there is nothing to look up from it."""
    return None if "scholar.google" in text else text


def find_authors(name, limit=5):
    data = get("authors", search=name, per_page=limit)
    out = []
    for a in data.get("results", []):
        insts = a.get("last_known_institutions") or []
        out.append({
            "id": a["id"].rsplit("/", 1)[-1],
            "name": a["display_name"],
            "works": a.get("works_count", 0),
            "cited": a.get("cited_by_count", 0),
            "institution": insts[0]["display_name"] if insts else "",
            "topics": [t["display_name"] for t in (a.get("topics") or [])[:8]],
        })
    return out


def fetch_corpus(author_id, max_works=400):
    """Walk the author's works, collecting coauthors, topics and title words."""
    coauthors = Counter()
    coauthor_display = {}
    topics = Counter()
    title_words = Counter()
    seen = 0
    cursor = "*"

    while seen < max_works and cursor:
        page = get("works", filter=f"author.id:{author_id}",
                   per_page=100, cursor=cursor,
                   select="title,topics,authorships")
        results = page.get("results", [])
        if not results:
            break
        for w in results:
            seen += 1
            for auth in w.get("authorships", []):
                person = auth.get("author") or {}
                pid = (person.get("id") or "").rsplit("/", 1)[-1]
                if not pid or pid == author_id:
                    continue
                coauthors[pid] += 1
                coauthor_display.setdefault(pid, person.get("display_name", ""))
            for t in (w.get("topics") or [])[:3]:
                topics[t["display_name"]] += 1
            for word in extract_terms(w.get("title") or ""):
                title_words[word] += 1
        cursor = (page.get("meta") or {}).get("next_cursor")

    return {"works_seen": seen, "coauthors": coauthors,
            "coauthor_display": coauthor_display, "topics": topics,
            "title_terms": title_words}


STOP = set("""a an the of and or in on for with to from at by as is are be been
we our this that these those new using via towards toward into over under near
between among within without through above below its their his her it he she
study studies theory model models system systems effect effects role case cases
approach approaches method methods result results analysis observation evidence
letter comment reply erratum note notes review paper papers arxiv physics
physical letters journal properties property behavior behaviour dynamics
structure structures formation general simple exact numerical experimental
theoretical anomalous novel first second third one two three high low large
small strong weak long short can may does do not no non self""".split())


# Many OpenAlex titles carry the publisher's raw MathML, so a naive tokeniser
# produces junk phrases like "mml mrow" and "xmlns mml". Strip the markup, then
# blocklist what survives it.
MARKUP = set("""mml mrow msub msup msubsup mfrac msqrt mi mo mn mtext math
mathml mstyle mspace munder mover munderover mtable mtr mtd semantics
annotation encoding xmlns mathvariant italic bold display inline block
altimg overflow separator stretchy w3 1998 http https www org com tex latex
xml href sub sup span div nbsp amp quot apos lt gt phys rev lett mod jour
journal vol pp doi arxiv preprint""".split())


def extract_terms(title):
    """Multi-word physics phrases are the useful signal, so keep bigrams too."""
    text = html.unescape(title)
    text = re.sub(r"<[^>]*>", " ", text)             # drop XML/MathML tags
    text = re.sub(r"https?://\S+", " ", text)
    text = unicodedata.normalize("NFKD", text.lower())
    text = re.sub(r"\$[^$]*\$", " ", text)           # drop inline maths
    text = re.sub(r"\\[a-z]+", " ", text)            # drop LaTeX commands
    text = re.sub(r"[^a-z0-9\- ]", " ", text)
    toks = [t for t in text.split()
            if len(t) > 2 and t not in STOP and t not in MARKUP
            and not t.isdigit()]
    terms = list(toks)
    for i in range(len(toks) - 1):
        terms.append(f"{toks[i]} {toks[i+1]}")
    return terms


def build_suggestions(corpus, min_coauthor=3, n_topics=14, n_terms=40,
                      max_coauthors=40):
    # Cap the list: a prolific author has hundreds of coauthors, and the tail is
    # peripheral collaborators whose own papers you would not want surfaced.
    coauthors = []
    for pid, n in corpus["coauthors"].most_common(300):
        name = corpus["coauthor_display"].get(pid, "")
        if n >= min_coauthor and name and fetch.name_key(name):
            coauthors.append((fetch.pretty_name(name), n))
        if len(coauthors) >= max_coauthors:
            break

    topics = [(t, n) for t, n in corpus["topics"].most_common(n_topics) if n >= 2]

    # Keep only multi-word phrases for keywords: single words are too noisy as
    # regexes ("order", "phase") and would match almost every abstract.
    terms = [(t, n) for t, n in corpus["title_terms"].most_common(400)
             if " " in t and n >= 3][:n_terms]

    return {"coauthors": coauthors, "topics": topics, "terms": terms}


def render_toml(prof, corpus, sug):
    L = []
    L.append("# " + "=" * 72)
    L.append(f"#  Generated from the publication record of {prof['name']}")
    L.append(f"#  {prof['institution']}" if prof["institution"] else "#")
    L.append(f"#  {corpus['works_seen']} papers read from OpenAlex "
             f"({prof['cited']} total citations)")
    L.append("#")
    L.append("#  Paste any of this into config.toml, or rerun with --apply.")
    L.append("#  Prune it — frequency is not the same as interest.")
    L.append("# " + "=" * 72)
    L.append("")
    L.append("# --- frequent coauthors, most frequent first ---")
    L.append("# The number is how many papers they share with him.")
    L.append("[authors]")
    L.append("names = [")
    for name, n in sug["coauthors"]:
        L.append(f'  "{name}",'.ljust(38) + f"# {n} joint papers")
    L.append("]")
    L.append("")
    L.append("# --- his own recurring subject areas, as classified by OpenAlex ---")
    for topic, n in sug["topics"]:
        L.append(f"#   {topic}  ({n} papers)")
    L.append("")
    L.append("# --- recurring phrases from his own titles, as a topic group ---")
    L.append("# Frequency counts are in the comments. Delete the ones that are")
    L.append("# incidental and raise the weight if this is the core of his work.")
    L.append("[[topics]]")
    L.append('name = "own-corpus"')
    L.append("weight = 4.0")
    L.append("keywords = [")
    for term, n in sug["terms"]:
        esc = term.replace("\\", "\\\\").replace('"', '\\"')
        L.append(f'  "{esc}",'.ljust(40) + f"# {n}")
    L.append("]")
    L.append("")
    return "\n".join(L)


def apply_to_config(sug, prof, corpus_size=0):
    """Merge suggestions into config.toml, keeping a backup. Never removes."""
    cfg = HOME / "config.toml"
    if not cfg.exists():
        raise SystemExit("config.toml not found")
    backup = HOME / "config.toml.backup"
    text = cfg.read_text(encoding="utf-8")
    backup.write_text(text, encoding="utf-8")

    # Parse the config rather than scanning lines: the author list packs several
    # names onto one line, and a per-line regex silently missed all but the first,
    # which let already-listed people get added a second time.
    with open(cfg, "rb") as fh:
        conf = tomllib.load(fh)
    existing_keys = set()
    for name in conf.get("authors", {}).get("names", []):
        k = fetch.name_key(name)
        if k:
            existing_keys.add(k)

    fresh = []
    for name, n in sug["coauthors"]:
        k = fetch.name_key(name)
        if k and k not in existing_keys:
            fresh.append((name, n))
            existing_keys.add(k)

    added_authors = 0
    if fresh:
        block = (f"\n  # --- added from {prof['name']}'s coauthors, with joint-paper\n"
                 f"  #     counts. Frequency is not interest: prune the ones who are\n"
                 f"  #     collaborators on experiments you would not read. ---\n"
                 + "".join(f'  "{n}",'.ljust(38) + f"# {c} joint\n"
                           for n, c in fresh))
        # insert just before the closing bracket of [authors].names
        m = re.search(r"(\[authors\]\s*\nnames = \[)(.*?)(\n\])", text, re.S)
        if not m:
            raise SystemExit("could not find [authors] names = [ ... ] in config.toml")
        text = text[:m.end(2)] + block.rstrip("\n") + text[m.end(2):]
        added_authors = len(fresh)

    # The reader description is the biggest single lever on what gets picked,
    # so set it from the profile too rather than leaving the generic default.
    areas = "; ".join(t for t, _ in sug["topics"][:8])
    where = f", {prof['institution']}" if prof["institution"] else ""
    desc = (f"\n{prof['name']}{where}. Across {corpus_size} indexed papers, "
            f"publishes on: {areas}.\n"
            f"Judge papers by whether this person would spend an evening on "
            f"them.\n")
    m = re.search(r'(\[reader\]\s*\ndescription\s*=\s*""")(.*?)(""")', text, re.S)
    if m:
        text = text[:m.end(1)] + desc + text[m.start(3):]
    else:
        text = (f'\n[reader]\ndescription = """{desc}"""\n\n') + text

    if sug["terms"] and "own-corpus" not in text:
        group = ["", "", "[[topics]]", 'name = "own-corpus"',
                 "# Phrases taken from the titles of his own papers.",
                 "weight = 4.0", "keywords = ["]
        for term, n in sug["terms"]:
            esc = term.replace("\\", "\\\\").replace('"', '\\"')
            group.append(f'  "{esc}",')
        group.append("]")
        text = text.rstrip() + "\n" + "\n".join(group) + "\n"

    cfg.write_text(text, encoding="utf-8")
    return added_authors, len(sug["terms"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("who", help="a researcher's name, or a Google Scholar URL")
    ap.add_argument("--apply", action="store_true",
                    help="merge the result into config.toml (keeps a backup)")
    ap.add_argument("--list", action="store_true",
                    help="only show matching people, don't build anything")
    ap.add_argument("--pick", type=int, default=0,
                    help="choose the Nth match instead of the best one")
    ap.add_argument("--max-works", type=int, default=400)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    name = scholar_hint(args.who)
    if name is None:
        print("A Google Scholar URL does not contain the person's name, and "
              "Scholar blocks automated lookups.\nRun it with the name instead, "
              "e.g.  python3 profile.py \"Ada Lovelace\"", file=sys.stderr)
        return 2

    try:
        matches = find_authors(name)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"Could not reach OpenAlex: {exc}", file=sys.stderr)
        return 2

    if not matches:
        print(f"No one found for {name!r}.", file=sys.stderr)
        return 1

    if args.list:
        if args.json:
            print(json.dumps(matches, indent=1))
        else:
            for i, m in enumerate(matches):
                print(f"[{i}] {m['name']} — {m['institution'] or 'unknown'}"
                      f"\n    {m['works']} papers, {m['cited']} citations"
                      f"\n    {', '.join(m['topics'][:4])}")
        return 0

    prof = matches[min(args.pick, len(matches) - 1)]
    if not args.json:
        print(f"Reading the corpus of {prof['name']} "
              f"({prof['institution'] or 'affiliation unknown'}) — "
              f"{prof['works']} papers...", file=sys.stderr)

    try:
        corpus = fetch_corpus(prof["id"], max_works=args.max_works)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"Could not read the corpus: {exc}", file=sys.stderr)
        return 2

    sug = build_suggestions(corpus)

    if args.json:
        print(json.dumps({
            "profile": prof,
            "works_seen": corpus["works_seen"],
            "coauthors": sug["coauthors"],
            "topics": sug["topics"],
            "terms": sug["terms"],
            "toml": render_toml(prof, corpus, sug),
        }, indent=1, ensure_ascii=False))
        return 0

    toml_text = render_toml(prof, corpus, sug)
    out = HOME / "profile-suggestions.toml"
    out.write_text(toml_text, encoding="utf-8")

    if args.apply:
        n_auth, n_terms = apply_to_config(sug, prof, corpus['works_seen'])
        print(f"\nconfig.toml updated: {n_auth} new authors, "
              f"{n_terms} keyword phrases.")
        print("Previous version saved as config.toml.backup")
    else:
        print(toml_text)
        print(f"\nWritten to {out.name}. Nothing in config.toml was changed.")
        print("Rerun with --apply to merge it in (a backup is kept).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
