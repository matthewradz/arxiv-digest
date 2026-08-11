#!/usr/bin/env python3
"""
Careful edits to config.toml that preserve the user's comments and layout.

Rewriting the file from parsed TOML would throw away every comment, and the
comments are most of what makes config.toml editable by hand. So we patch the
text — but patching text needs to be exact, and it is easy to get wrong:

  * `[authors]` may have comment lines between the header and `names`
  * `names` may be `names = []` on one line, or spread over many lines
  * a regex that assumes `\\n]` closes the array will happily match a `]`
    belonging to a completely different table further down the file, and
    silently write author names into a topic's keyword list

Both of those bit this project, so the parsing lives here, once, with tests.
"""

import re

AUTHORS_NAMES = re.compile(r"(names\s*=\s*)\[[^\]]*\]", re.S)


def _authors_span(text):
    """(start, end) of the [authors] table body, or None."""
    m = re.search(r"^\[authors\][^\n]*\n", text, re.M)
    if not m:
        return None
    nxt = re.search(r"^\[", text[m.end():], re.M)
    return m.end(), (m.end() + nxt.start() if nxt else len(text))


def read_authors(text):
    """The author names currently listed, in file order."""
    span = _authors_span(text)
    if not span:
        return []
    body = text[span[0]:span[1]]
    m = AUTHORS_NAMES.search(body)
    if not m:
        return []
    return re.findall(r'"([^"]*)"', m.group(0))


def render_names(entries, indent="  "):
    """
    entries: list of (name, comment_or_None). Rendered one per line so that
    per-name comments (joint-paper counts, provenance) survive.
    """
    lines = ["names = ["]
    for name, comment in entries:
        esc = name.replace("\\", "\\\\").replace('"', '\\"')
        row = f'{indent}"{esc}",'
        lines.append(f"{row.ljust(38)}# {comment}" if comment else row)
    lines.append("]")
    return "\n".join(lines)


def set_authors(text, entries, header_comment=None):
    """
    Replace the [authors] names array with `entries`, creating the table if it
    is missing. Returns the new text.
    """
    block = render_names(entries)
    if header_comment:
        block = f"# {header_comment}\n{block}"

    span = _authors_span(text)
    if span:
        body = text[span[0]:span[1]]
        m = AUTHORS_NAMES.search(body)
        if m:
            new_body = body[:m.start()] + block + body[m.end():]
            return text[:span[0]] + new_body + text[span[1]:]
        return text[:span[1]].rstrip() + "\n" + block + "\n" + text[span[1]:]
    return text.rstrip() + "\n\n[authors]\n" + block + "\n"


def topic_names(text):
    return re.findall(r'^\s*name\s*=\s*"([^"]+)"', text, re.M)


def topic_blocks(text):
    """Whole [[topics]] blocks, as text, so they can be copied verbatim."""
    return re.findall(r"(\[\[topics\]\]\n(?:.*?\n)*?\])\n", text)


def append_topics(text, blocks, note=None):
    if not blocks:
        return text
    head = f"\n\n# {note}\n" if note else "\n\n"
    return text.rstrip() + head + "\n\n".join(blocks) + "\n"


def set_scalar(text, key, value, table=None):
    """Set `key = "value"` in place, preserving any trailing comment."""
    pat = re.compile(rf'^(\s*{re.escape(key)}\s*=\s*)"[^"]*"', re.M)
    new, n = pat.subn(lambda m: m.group(1) + f'"{value}"', text, count=1)
    if n:
        return new
    prefix = f"\n[{table}]\n" if table else "\n"
    return text.rstrip() + prefix + f'{key} = "{value}"\n'


# --------------------------------------------------------------------------
#  self-test — run `python3 configedit.py` after touching anything here
# --------------------------------------------------------------------------

def _test():
    import tomllib
    cases = {
        "empty inline": '[settings]\nx = 1\n\n[authors]\nnames = []\n\n[[topics]]\nname = "t"\nkeywords = ["a"]\n',
        "commented": '[authors]\n# a comment\n# another\nnames = []\n\n[[topics]]\nname = "t"\nkeywords = ["a"]\n',
        "multiline": '[authors]\nnames = [\n  "Ada Lovelace",\n  "Alan Turing",\n]\n\n[[topics]]\nname = "t"\nkeywords = ["a"]\n',
        "no table": '[settings]\nx = 1\n',
    }
    for label, src in cases.items():
        before = read_authors(src)
        out = set_authors(src, [(n, None) for n in before] + [("Grace Hopper", "3 joint")])
        conf = tomllib.loads(out)          # must still parse
        names = conf.get("authors", {}).get("names", [])
        assert "Grace Hopper" in names, (label, names)
        assert all(b in names for b in before), (label, before, names)
        # the crucial regression: nothing may leak into a topic's keywords
        for t in conf.get("topics", []):
            assert "Grace Hopper" not in t.get("keywords", []), label
        print(f"  ok  {label:12s} {len(before)} -> {len(names)} authors, "
              f"topics intact")

    src = '[settings]\ndownload_dir = ""   # where to save\n'
    out = set_scalar(src, "download_dir", "~/Papers")
    assert '"~/Papers"' in out and "# where to save" in out
    print("  ok  set_scalar keeps trailing comments")
    print("all configedit self-tests passed")


if __name__ == "__main__":
    _test()
