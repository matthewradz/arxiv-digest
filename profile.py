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


def find_institution(query):
    """Resolve 'MIT' or 'Massachusetts Institute of Technology' to an id."""
    try:
        data = get("institutions", search=query, per_page=1)
    except (urllib.error.URLError, TimeoutError):
        return None
    for i in data.get("results", []):
        return {"id": i["id"].rsplit("/", 1)[-1], "name": i["display_name"]}
    return None


def _norm(s):
    return re.sub(r"[^a-z ]", "", fetch.deaccent(s).lower()).strip()


def find_authors(name, limit=8, institution=None):
    """
    Look someone up, optionally narrowed to an institution.

    Two things make common names findable. First, the institution filter —
    'Qi Liu' alone returns 3,760 people ranked by output, none of them the one
    you want. Second, exact-name-first ranking: OpenAlex's search is fuzzy and
    will happily return 'Enqi Liu' and 'Stephen Forrest' for 'Qi Liu', so an
    exact match on the display name is promoted above a more prolific near-miss.
    """
    params = {"search": name, "per_page": max(limit, 25)}
    inst = None
    if institution:
        inst = find_institution(institution)
        if inst:
            # NB: the working filter is affiliations.institution.id.
            # last_known_institutions.id silently returns zero results.
            params["filter"] = f"affiliations.institution.id:{inst['id']}"
    data = get("authors", **params)

    wanted = _norm(name)
    wanted_parts = set(wanted.split())
    out = []
    for a in data.get("results", []):
        insts = a.get("last_known_institutions") or []
        display = a["display_name"]
        norm = _norm(display)
        # OpenAlex search is fuzzy enough to return "Stephen R. Forrest" for
        # "Qi Liu". Require at least one shared name part, or it is noise.
        if not (wanted_parts & set(norm.split())):
            continue
        last_known = insts[0]["display_name"] if insts else ""
        out.append({
            "id": a["id"].rsplit("/", 1)[-1],
            "name": display,
            "works": a.get("works_count", 0),
            "cited": a.get("cited_by_count", 0),
            # When filtering by institution, show the one that was searched:
            # OpenAlex's "last known" is often stale, so a person who really is
            # at MIT can show up labelled with a former employer, which reads
            # like the filter silently failed.
            "institution": inst["name"] if inst else last_known,
            "last_known": last_known,
            "via_institution": bool(inst),
            "topics": [t["display_name"] for t in (a.get("topics") or [])[:8]],
            "exact": norm == wanted,
        })
    out.sort(key=lambda a: (not a["exact"], -a["cited"]))
    return out[:limit]


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


def merge_corpora(corpora):
    """
    Combine several people into one profile weighted towards their overlap.

    With one person this is just their corpus. With two or three, a phrase that
    shows up in more than one person's titles is what they actually share, so it
    is scored far above something only one of them writes about — that is the
    point of adding a second name. Coauthors are unioned rather than
    intersected: everyone's collaborators are worth seeing.
    """
    if len(corpora) == 1:
        return corpora[0], {}

    merged = {"works_seen": sum(c["works_seen"] for c in corpora),
              "coauthors": Counter(), "coauthor_display": {},
              "topics": Counter(), "title_terms": Counter()}
    for c in corpora:
        merged["coauthors"].update(c["coauthors"])
        merged["coauthor_display"].update(c["coauthor_display"])
        merged["topics"].update(c["topics"])

    # How many of the people use each phrase / topic at all.
    term_people = Counter()
    topic_people = Counter()
    for c in corpora:
        term_people.update(set(c["title_terms"]))
        topic_people.update(set(c["topics"]))

    shared_terms, shared_topics = [], []
    for term, people in term_people.items():
        total = sum(c["title_terms"].get(term, 0) for c in corpora)
        if people > 1:
            # Overlap is the signal: weight it far above a solo interest.
            merged["title_terms"][term] = total * 10 * people
            shared_terms.append(term)
        else:
            merged["title_terms"][term] = total
    shared_topics = [t for t, n in topic_people.items() if n > 1]

    return merged, {"shared_terms": shared_terms,
                    "shared_topics": shared_topics}


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


def _who(profiles):
    return " + ".join(f"{p['name']}"
                      + (f" ({p['institution']})" if p["institution"] else "")
                      for p in profiles)


def render_toml(profiles, corpus, sug, overlap=None):
    overlap = overlap or {}
    L = []
    L.append("# " + "=" * 72)
    L.append(f"#  Generated from the publication record of:")
    for prof in profiles:
        L.append(f"#    {prof['name']}"
                 + (f" — {prof['institution']}" if prof["institution"] else "")
                 + f"  ({prof['works']} papers, {prof['cited']} citations)")
    L.append(f"#  {corpus['works_seen']} papers read from OpenAlex")
    if len(profiles) > 1:
        L.append("#")
        L.append(f"#  {len(overlap.get('shared_terms', []))} phrases appear in "
                 f"more than one of these corpora. Those are the overlap, and")
        L.append("#  they are weighted far above anything only one person writes about.")
    L.append("#")
    L.append("#  Paste any of this into config.toml, or rerun with --apply.")
    L.append("#  Prune it — frequency is not the same as interest.")
    L.append("# " + "=" * 72)
    L.append("")
    L.append("# --- frequent coauthors, most frequent first ---")
    L.append("[authors]")
    L.append("names = [")
    for name, n in sug["coauthors"]:
        L.append(f'  "{name}",'.ljust(38) + f"# {n} joint papers")
    L.append("]")
    L.append("")
    L.append("# --- recurring subject areas, as classified by OpenAlex ---")
    shared_topics = set(overlap.get("shared_topics", []))
    for topic, n in sug["topics"]:
        mark = "  <- shared" if topic in shared_topics else ""
        L.append(f"#   {topic}  ({n} papers){mark}")
    L.append("")
    L.append("# --- recurring phrases from their own titles ---")
    L.append("[[topics]]")
    L.append('name = "own-corpus"')
    L.append("weight = 4.0")
    L.append("keywords = [")
    shared = set(overlap.get("shared_terms", []))
    for term, n in sug["terms"]:
        esc = term.replace("\\", "\\\\").replace('"', '\\"')
        mark = "  shared" if term in shared else ""
        L.append(f'  "{esc}",'.ljust(40) + f"#{mark}")
    L.append("]")
    L.append("")
    return "\n".join(L)


def apply_to_config(sug, profiles, corpus_size=0, overlap=None):
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
        block = (f"\n  # --- added from the coauthors of {_who(profiles)},\n"
                 f"  #     with joint-paper counts. Frequency is not interest:\n"
                 f"  #     prune collaborators whose own papers you would not read. ---\n"
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
    overlap = overlap or {}
    if len(profiles) == 1:
        subject = _who(profiles)
        desc = (f"\n{subject}. Across {corpus_size} indexed papers, "
                f"publishes on: {areas}.\n"
                f"Judge papers by whether this person would spend an evening "
                f"on them.\n")
    else:
        shared = ", ".join(overlap.get("shared_topics", [])[:6]) or areas
        desc = (f"\nA group reading together: {_who(profiles)}.\n"
                f"Across {corpus_size} indexed papers between them they work on: "
                f"{areas}.\n"
                f"Their common ground is: {shared}. Weight papers that sit in "
                f"that overlap most heavily — a paper only one of them would "
                f"care about is worth less than one they would all read.\n")
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
    ap = argparse.ArgumentParser(
        description="Build author and topic lists from real publication records.")
    ap.add_argument("who", nargs="+",
                    help="one to three researcher names; with more than one, "
                         "their shared interests are weighted most")
    ap.add_argument("--at", action="append", default=[],
                    help="institution for the matching name, e.g. --at MIT "
                         "(repeat once per name, or omit)")
    ap.add_argument("--apply", action="store_true",
                    help="merge the result into config.toml (keeps a backup)")
    ap.add_argument("--list", action="store_true",
                    help="only show matching people, don't build anything")
    ap.add_argument("--pick", action="append", default=[],
                    help="choose the Nth match for the matching name")
    ap.add_argument("--max-works", type=int, default=400)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    names = [scholar_hint(w) for w in args.who[:3]]
    if any(n is None for n in names):
        print("A Google Scholar URL does not contain the person's name, and "
              "Scholar blocks automated lookups.\nUse the name instead, e.g.  "
              "python3 profile.py \"Ada Lovelace\" --at MIT", file=sys.stderr)
        return 2

    def nth(lst, i, default=None):
        return lst[i] if i < len(lst) else default

    # --- resolve each person -------------------------------------------------
    profiles, all_matches = [], []
    for i, nm in enumerate(names):
        where = nth(args.at, i)
        try:
            matches = find_authors(nm, institution=where)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"Could not reach OpenAlex: {exc}", file=sys.stderr)
            return 2
        if not matches:
            extra = f" at {where}" if where else ""
            print(f"No one found for {nm!r}{extra}.", file=sys.stderr)
            if where:
                print("Try the institution's full name, or drop --at.",
                      file=sys.stderr)
            return 1
        all_matches.append(matches)
        profiles.append(matches[min(int(nth(args.pick, i, 0) or 0),
                                    len(matches) - 1)])

    if args.list:
        if args.json:
            print(json.dumps(all_matches, indent=1))
        else:
            for nm, matches in zip(names, all_matches):
                print(f"--- {nm} ---")
                for i, m in enumerate(matches):
                    star = " *exact name match*" if m.get("exact") else ""
                    where = m["institution"] or "unknown"
                    if m.get("via_institution"):
                        where = f"affiliated with {where}"
                        if m.get("last_known") and m["last_known"] != m["institution"]:
                            where += f"; now listed at {m['last_known']}"
                    print(f"[{i}] {m['name']} — {where}{star}"
                          f"\n    {m['works']} papers, {m['cited']} citations"
                          f"\n    {', '.join(m['topics'][:4])}")
        return 0

    # --- read each corpus ----------------------------------------------------
    corpora = []
    for prof in profiles:
        if not args.json:
            print(f"Reading {prof['name']} "
                  f"({prof['institution'] or 'affiliation unknown'}) — "
                  f"{prof['works']} papers...", file=sys.stderr)
        try:
            corpora.append(fetch_corpus(prof["id"], max_works=args.max_works))
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"Could not read the corpus: {exc}", file=sys.stderr)
            return 2

    corpus, overlap = merge_corpora(corpora)
    sug = build_suggestions(corpus)

    if args.json:
        print(json.dumps({
            "profiles": profiles,
            "works_seen": corpus["works_seen"],
            "coauthors": sug["coauthors"],
            "topics": sug["topics"],
            "terms": sug["terms"],
            "shared_terms": overlap.get("shared_terms", [])[:40],
            "shared_topics": overlap.get("shared_topics", []),
            "toml": render_toml(profiles, corpus, sug, overlap),
        }, indent=1, ensure_ascii=False))
        return 0

    toml_text = render_toml(profiles, corpus, sug, overlap)
    out = HOME / "profile-suggestions.toml"
    out.write_text(toml_text, encoding="utf-8")

    if args.apply:
        n_auth, n_terms = apply_to_config(sug, profiles, corpus["works_seen"],
                                          overlap)
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
