#!/usr/bin/env python3
"""
The reader app: a small local web page for the nightly arXiv digest.

Double-clicking the app runs this. It starts a server on localhost, kicks off
tonight's build, opens the browser, and shuts itself down when left idle.

    python3 app.py                 build tonight's digest and open the reader
    python3 app.py --no-build      just read what is already there
    python3 app.py --no-open       don't launch a browser (for testing)

Everything it shows comes from the same files run.sh produces, so the shell
tools and this app stay interchangeable.
"""

import argparse
import html
import json
import re
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import engine
import pick

HOME = Path(__file__).resolve().parent
PORT_FILE = HOME / "runs" / ".app-port"
IDLE_TIMEOUT = 45 * 60          # quit after this long with nobody reading
ID_RE = r"\d{4}\.\d{4,5}"

STATE = {
    "phase": "idle",            # idle | building | ready | error | uptodate
    "message": "",
    "lines": [],
    "date": None,
    "learn": "idle",
    "learn_message": "",
    "last_seen": time.time(),
}
LOCK = threading.Lock()


# ==========================================================================
#  running the pipeline
# ==========================================================================

def resolve_env():
    """One definition of the subprocess environment, shared with the pipeline."""
    return engine.env_for_subprocess()


def set_state(**kw):
    with LOCK:
        STATE.update(kw)


def push_line(text):
    with LOCK:
        STATE["lines"].append(text)
        STATE["lines"] = STATE["lines"][-40:]
        STATE["message"] = text


def build_thread(force=False):
    set_state(phase="building", message="Checking tonight's listing...",
              lines=[])
    env = resolve_env()
    if not env.get("CLAUDE_BIN") and not env.get("CODEX_BIN"):
        set_state(phase="error",
                  message="No model CLI was found. Install Claude Code (for a "
                          "Claude Pro/Max plan) or Codex CLI (for a ChatGPT "
                          "Plus/Pro plan), sign in, then try again.")
        return

    cmd = [sys.executable, str(HOME / "pipeline.py"), "digest"] \
        + (["--force"] if force else [])
    try:
        proc = subprocess.Popen(cmd, cwd=str(HOME), env=env,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                bufsize=1)
    except OSError as exc:
        set_state(phase="error", message=f"Could not start the pipeline: {exc}")
        return

    already = False
    for raw in proc.stdout:
        line = raw.rstrip()
        if not line:
            continue
        if "ALREADY_DIGESTED" in line:
            already = True
        if line.startswith(("LISTING_DATE", "TOTAL", "BRIEF", "===")):
            if line.startswith("LISTING_DATE"):
                set_state(date=line.split()[-1])
            continue
        push_line(line)
    proc.wait()

    latest = latest_date()
    if proc.returncode in (0, 3):
        set_state(phase="uptodate" if already else "ready", date=latest,
                  message="Already up to date." if already
                          else "Digest ready.")
    else:
        set_state(phase="error", date=latest,
                  message=STATE["message"] or
                          f"the pipeline exited with status {proc.returncode}.")


def learn_thread():
    set_state(learn="running", learn_message="Reading your picks...")
    env = resolve_env()
    try:
        proc = subprocess.run(
            [sys.executable, str(HOME / "pipeline.py"), "learn"],
            cwd=str(HOME), env=env, capture_output=True, text=True,
            timeout=900)
    except (OSError, subprocess.TimeoutExpired) as exc:
        set_state(learn="error", learn_message=f"learn failed: {exc}")
        return
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        set_state(learn="error", learn_message=out.strip()[-600:] or "failed")
        return
    if "Nothing marked yet" in out or "No picks recorded yet" in out:
        set_state(learn="done",
                  learn_message="No picks recorded yet — mark a few papers as "
                                "downloaded first.")
        return
    set_state(learn="done",
              learn_message="Preferences updated. Tomorrow's digest will use "
                            "them. Suggested config changes are in "
                            "config-suggestions.md.")


# ==========================================================================
#  reading what is on disk
# ==========================================================================

SETUP_DONE = HOME / ".setup-done"

# The full arXiv category taxonomy, so any field can use this — not just
# condensed matter. Physics archives are listed first because that is who this
# was built for, but every archive is selectable.
# Generated from https://arxiv.org/category_taxonomy — 155 categories.
# (archive_code, archive_name, [(category_code, category_name), ...])
ARXIV_TAXONOMY = [
    ('quant-ph', 'Quantum Physics', [
    ]),
    ('cond-mat', 'Condensed Matter', [
        ('cond-mat.dis-nn', 'Disordered Systems and Neural Networks'),
        ('cond-mat.mes-hall', 'Mesoscale and Nanoscale Physics'),
        ('cond-mat.mtrl-sci', 'Materials Science'),
        ('cond-mat.other', 'Other Condensed Matter'),
        ('cond-mat.quant-gas', 'Quantum Gases'),
        ('cond-mat.soft', 'Soft Condensed Matter'),
        ('cond-mat.stat-mech', 'Statistical Mechanics'),
        ('cond-mat.str-el', 'Strongly Correlated Electrons'),
        ('cond-mat.supr-con', 'Superconductivity'),
    ]),
    ('hep-th', 'High Energy Physics — Theory', [
    ]),
    ('hep-ph', 'High Energy Physics — Phenomenology', [
    ]),
    ('hep-ex', 'High Energy Physics — Experiment', [
    ]),
    ('hep-lat', 'High Energy Physics — Lattice', [
    ]),
    ('gr-qc', 'General Relativity and Quantum Cosmology', [
    ]),
    ('nucl-th', 'Nuclear Theory', [
    ]),
    ('nucl-ex', 'Nuclear Experiment', [
    ]),
    ('astro-ph', 'Astrophysics', [
        ('astro-ph.CO', 'Cosmology and Nongalactic Astrophysics'),
        ('astro-ph.EP', 'Earth and Planetary Astrophysics'),
        ('astro-ph.GA', 'Astrophysics of Galaxies'),
        ('astro-ph.HE', 'High Energy Astrophysical Phenomena'),
        ('astro-ph.IM', 'Instrumentation and Methods for Astrophysics'),
        ('astro-ph.SR', 'Solar and Stellar Astrophysics'),
    ]),
    ('physics', 'Physics', [
        ('physics.acc-ph', 'Accelerator Physics'),
        ('physics.ao-ph', 'Atmospheric and Oceanic Physics'),
        ('physics.app-ph', 'Applied Physics'),
        ('physics.atm-clus', 'Atomic and Molecular Clusters'),
        ('physics.atom-ph', 'Atomic Physics'),
        ('physics.bio-ph', 'Biological Physics'),
        ('physics.chem-ph', 'Chemical Physics'),
        ('physics.class-ph', 'Classical Physics'),
        ('physics.comp-ph', 'Computational Physics'),
        ('physics.data-an', 'Data Analysis, Statistics and Probability'),
        ('physics.ed-ph', 'Physics Education'),
        ('physics.flu-dyn', 'Fluid Dynamics'),
        ('physics.gen-ph', 'General Physics'),
        ('physics.geo-ph', 'Geophysics'),
        ('physics.hist-ph', 'History and Philosophy of Physics'),
        ('physics.ins-det', 'Instrumentation and Detectors'),
        ('physics.med-ph', 'Medical Physics'),
        ('physics.optics', 'Optics'),
        ('physics.plasm-ph', 'Plasma Physics'),
        ('physics.pop-ph', 'Popular Physics'),
        ('physics.soc-ph', 'Physics and Society'),
        ('physics.space-ph', 'Space Physics'),
    ]),
    ('math-ph', 'Mathematical Physics', [
    ]),
    ('nlin', 'Nonlinear Sciences', [
        ('nlin.AO', 'Adaptation and Self-Organizing Systems'),
        ('nlin.CD', 'Chaotic Dynamics'),
        ('nlin.CG', 'Cellular Automata and Lattice Gases'),
        ('nlin.PS', 'Pattern Formation and Solitons'),
        ('nlin.SI', 'Exactly Solvable and Integrable Systems'),
    ]),
    ('math', 'Mathematics', [
        ('math.AC', 'Commutative Algebra'),
        ('math.AG', 'Algebraic Geometry'),
        ('math.AP', 'Analysis of PDEs'),
        ('math.AT', 'Algebraic Topology'),
        ('math.CA', 'Classical Analysis and ODEs'),
        ('math.CO', 'Combinatorics'),
        ('math.CT', 'Category Theory'),
        ('math.CV', 'Complex Variables'),
        ('math.DG', 'Differential Geometry'),
        ('math.DS', 'Dynamical Systems'),
        ('math.FA', 'Functional Analysis'),
        ('math.GM', 'General Mathematics'),
        ('math.GN', 'General Topology'),
        ('math.GR', 'Group Theory'),
        ('math.GT', 'Geometric Topology'),
        ('math.HO', 'History and Overview'),
        ('math.IT', 'Information Theory'),
        ('math.KT', 'K-Theory and Homology'),
        ('math.LO', 'Logic'),
        ('math.MG', 'Metric Geometry'),
        ('math.MP', 'Mathematical Physics'),
        ('math.NA', 'Numerical Analysis'),
        ('math.NT', 'Number Theory'),
        ('math.OA', 'Operator Algebras'),
        ('math.OC', 'Optimization and Control'),
        ('math.PR', 'Probability'),
        ('math.QA', 'Quantum Algebra'),
        ('math.RA', 'Rings and Algebras'),
        ('math.RT', 'Representation Theory'),
        ('math.SG', 'Symplectic Geometry'),
        ('math.SP', 'Spectral Theory'),
        ('math.ST', 'Statistics Theory'),
    ]),
    ('cs', 'Computer Science', [
        ('cs.AI', 'Artificial Intelligence'),
        ('cs.AR', 'Hardware Architecture'),
        ('cs.CC', 'Computational Complexity'),
        ('cs.CE', 'Computational Engineering, Finance, and Science'),
        ('cs.CG', 'Computational Geometry'),
        ('cs.CL', 'Computation and Language'),
        ('cs.CR', 'Cryptography and Security'),
        ('cs.CV', 'Computer Vision and Pattern Recognition'),
        ('cs.CY', 'Computers and Society'),
        ('cs.DB', 'Databases'),
        ('cs.DC', 'Distributed, Parallel, and Cluster Computing'),
        ('cs.DL', 'Digital Libraries'),
        ('cs.DM', 'Discrete Mathematics'),
        ('cs.DS', 'Data Structures and Algorithms'),
        ('cs.ET', 'Emerging Technologies'),
        ('cs.FL', 'Formal Languages and Automata Theory'),
        ('cs.GL', 'General Literature'),
        ('cs.GR', 'Graphics'),
        ('cs.GT', 'Computer Science and Game Theory'),
        ('cs.HC', 'Human-Computer Interaction'),
        ('cs.IR', 'Information Retrieval'),
        ('cs.IT', 'Information Theory'),
        ('cs.LG', 'Machine Learning'),
        ('cs.LO', 'Logic in Computer Science'),
        ('cs.MA', 'Multiagent Systems'),
        ('cs.MM', 'Multimedia'),
        ('cs.MS', 'Mathematical Software'),
        ('cs.NA', 'Numerical Analysis'),
        ('cs.NE', 'Neural and Evolutionary Computing'),
        ('cs.NI', 'Networking and Internet Architecture'),
        ('cs.OH', 'Other Computer Science'),
        ('cs.OS', 'Operating Systems'),
        ('cs.PF', 'Performance'),
        ('cs.PL', 'Programming Languages'),
        ('cs.RO', 'Robotics'),
        ('cs.SC', 'Symbolic Computation'),
        ('cs.SD', 'Sound'),
        ('cs.SE', 'Software Engineering'),
        ('cs.SI', 'Social and Information Networks'),
        ('cs.SY', 'Systems and Control'),
    ]),
    ('stat', 'Statistics', [
        ('stat.AP', 'Applications'),
        ('stat.CO', 'Computation'),
        ('stat.ME', 'Methodology'),
        ('stat.ML', 'Machine Learning'),
        ('stat.OT', 'Other Statistics'),
        ('stat.TH', 'Statistics Theory'),
    ]),
    ('q-bio', 'Quantitative Biology', [
        ('q-bio.BM', 'Biomolecules'),
        ('q-bio.CB', 'Cell Behavior'),
        ('q-bio.GN', 'Genomics'),
        ('q-bio.MN', 'Molecular Networks'),
        ('q-bio.NC', 'Neurons and Cognition'),
        ('q-bio.OT', 'Other Quantitative Biology'),
        ('q-bio.PE', 'Populations and Evolution'),
        ('q-bio.QM', 'Quantitative Methods'),
        ('q-bio.SC', 'Subcellular Processes'),
        ('q-bio.TO', 'Tissues and Organs'),
    ]),
    ('q-fin', 'Quantitative Finance', [
        ('q-fin.CP', 'Computational Finance'),
        ('q-fin.EC', 'Economics'),
        ('q-fin.GN', 'General Finance'),
        ('q-fin.MF', 'Mathematical Finance'),
        ('q-fin.PM', 'Portfolio Management'),
        ('q-fin.PR', 'Pricing of Securities'),
        ('q-fin.RM', 'Risk Management'),
        ('q-fin.ST', 'Statistical Finance'),
        ('q-fin.TR', 'Trading and Market Microstructure'),
    ]),
    ('eess', 'Electrical Engineering and Systems Science', [
        ('eess.AS', 'Audio and Speech Processing'),
        ('eess.IV', 'Image and Video Processing'),
        ('eess.SP', 'Signal Processing'),
        ('eess.SY', 'Systems and Control'),
    ]),
    ('econ', 'Economics', [
        ('econ.EM', 'Econometrics'),
        ('econ.GN', 'General Economics'),
        ('econ.TH', 'Theoretical Economics'),
    ]),
]



def bootstrap_config():
    """A fresh checkout ships config.default.toml; make it the live config."""
    live = HOME / "config.toml"
    default = HOME / "config.default.toml"
    if not live.exists() and default.exists():
        live.write_text(default.read_text(encoding="utf-8"), encoding="utf-8")
        return True
    return False


def read_feeds():
    """Sections currently configured. Empty means the user has not chosen yet."""
    cfg = HOME / "config.toml"
    if not cfg.exists():
        return []
    m = re.search(r"^\s*feeds\s*=\s*\[([^\]]*)\]",
                  cfg.read_text(encoding="utf-8"), re.M)
    return re.findall(r'"([^"]+)"', m.group(1)) if m else []


def write_feeds(feeds):
    """Rewrite the feeds line in config.toml, preserving its comment."""
    valid = [f for f in feeds
             if re.fullmatch(r"[a-z-]+(\.[A-Za-z-]+)?", str(f))]
    if not valid:
        return False, "pick at least one section"
    cfg = HOME / "config.toml"
    if not cfg.exists():
        return False, "config.toml is missing"
    text = cfg.read_text(encoding="utf-8")
    listing = ", ".join(f'"{f}"' for f in dict.fromkeys(valid))
    new, n = re.subn(r"^(\s*feeds\s*=\s*)\[[^\]]*\]",
                     lambda m: m.group(1) + "[" + listing + "]",
                     text, count=1, flags=re.M)
    if not n:
        return False, "could not find the feeds setting in config.toml"
    cfg.write_text(new, encoding="utf-8")
    return True, valid


def write_download_dir(path):
    cfg = HOME / "config.toml"
    if not cfg.exists():
        return False, "config.toml is missing"
    path = str(path or "").strip()
    if path and not re.fullmatch(r"[~/][^\"\n]*", path):
        return False, "give a full path, e.g. ~/Papers/arxiv"
    text = cfg.read_text(encoding="utf-8")
    new, n = re.subn(r'^(\s*download_dir\s*=\s*)"[^"]*"',
                     lambda m: m.group(1) + f'"{path}"', text, count=1,
                     flags=re.M)
    if not n:
        new = text.rstrip() + f'\n\n[settings]\ndownload_dir = "{path}"\n'
    cfg.write_text(new, encoding="utf-8")
    return True, path


def probe_engine(which, timeout=120):
    """Actually call the CLI, so we test sign-in rather than mere presence."""
    env = resolve_env()
    exe = env.get("CLAUDE_BIN" if which == "claude" else "CODEX_BIN")
    if not exe:
        return {"installed": False, "signed_in": False, "detail": "not installed"}
    if which == "claude":
        cmd = [exe, "-p", "Reply with exactly: READY"]
    else:
        cmd = [exe, "exec", "--skip-git-repo-check", "--ephemeral",
               "Reply with exactly: READY"]
    try:
        proc = subprocess.run(cmd, cwd=str(HOME), env=env, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"installed": True, "signed_in": False,
                "detail": "timed out — try running it once in a terminal first"}
    except OSError as exc:
        return {"installed": True, "signed_in": False, "detail": str(exc)}
    blob = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0 and "READY" in blob.upper():
        return {"installed": True, "signed_in": True, "detail": "signed in"}
    tail = " ".join(blob.split())[-300:] or f"exit {proc.returncode}"
    return {"installed": True, "signed_in": False, "detail": tail}


def run_profile(cli_args, timeout=300):
    """Run profile.py and return (ok, parsed-json-or-error-string)."""
    try:
        proc = subprocess.run(
            [sys.executable, str(HOME / "profile.py")] + cli_args,
            cwd=str(HOME), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"lookup failed: {exc}"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "lookup failed").strip()
    if "--json" not in cli_args:
        return True, (proc.stdout or "").strip()
    try:
        return True, json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False, "could not read the lookup result"


def digest_dates():
    return sorted((p.stem for p in (HOME / "digests").glob("*.md")),
                  reverse=True)


def latest_date():
    dates = digest_dates()
    return dates[0] if dates else None


def run_stats(date):
    """Counts for the header, straight from the stored run."""
    f = HOME / "runs" / date / "items.json"
    if not f.exists():
        return {}
    items = json.loads(f.read_text(encoding="utf-8"))["items"]
    fresh = [i for i in items if not i.get("is_replacement")]
    return {"total": len(items), "new": len(fresh),
            "replacements": len(items) - len(fresh)}


# ==========================================================================
#  markdown -> html (only the subset the digest actually uses)
# ==========================================================================

def inline(text):
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                 r'<a href="\2" target="_blank" rel="noopener">\1</a>', out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", out)
    return out


def blocks_to_html(lines):
    out, para, bullets = [], [], []

    def flush():
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para.clear()
        if bullets:
            out.append("<ul>" + "".join(f"<li>{inline(b)}</li>"
                                        for b in bullets) + "</ul>")
            bullets.clear()

    for line in lines:
        s = line.strip()
        if not s:
            flush()
        elif s.startswith("- "):
            if para:
                flush()
            bullets.append(s[2:])
        elif s.startswith("#"):
            flush()
            level = len(s) - len(s.lstrip("#"))
            out.append(f"<h{level+1}>{inline(s.lstrip('# '))}</h{level+1}>")
        else:
            if bullets:
                flush()
            para.append(s)
    flush()
    return "\n".join(out)


def first_id(text):
    m = re.search(ID_RE, text)
    return m.group(0) if m else None


PICK_OFF = "Want this"
PICK_ON = "Want this · click to undo"

EXPLAINER = """
<p class="explain">Click <b>Want this</b> on the ones you'd actually download —
clicking it again takes it back. That's the only thing the digest learns from.
Once there are a dozen or so, press <b>Retune from my picks</b> at the bottom and
it starts ranking to your taste.</p>"""


def pick_button(pid, picked):
    if not pid:
        return ""
    cls = "pick on" if picked else "pick"
    label = PICK_ON if picked else PICK_OFF
    hint = ("Recorded. Click again to take it back."
            if picked else
            "Tell the digest you want this paper. Click again to undo.")
    return (f'<button class="{cls}" data-id="{pid}" title="{hint}" '
            f'aria-pressed="{"true" if picked else "false"}">'
            f'<span class="tick">✓</span><span class="lbl">{label}</span>'
            f'</button>')


def render_five(lines, picks):
    entries, cur = [], None
    for line in lines:
        if line.startswith("### "):
            if cur:
                entries.append(cur)
            cur = {"head": line[4:].strip(), "meta": "", "why": "", "body": []}
        elif cur is None:
            continue
        elif line.strip().startswith("`arXiv:") and not cur["meta"]:
            cur["meta"] = line.strip()
        elif re.match(r"\*\*(Claim|Why):\*\*", line.strip()):
            cur["why"] = re.sub(r"^\*\*(Claim|Why):\*\*\s*", "",
                                line.strip())
        else:
            cur["body"].append(line)
    if cur:
        entries.append(cur)

    cards = []
    for e in entries:
        pid = first_id(e["meta"]) or first_id(e["head"])
        num, _, title = e["head"].partition(". ")
        if not title:
            num, title = "", e["head"]
        links = ""
        if pid:
            links = (f'<a class="btn" href="https://arxiv.org/abs/{pid}" '
                     f'target="_blank" rel="noopener">abstract</a>'
                     f'<a class="btn" href="https://arxiv.org/pdf/{pid}" '
                     f'target="_blank" rel="noopener">PDF</a>')
        # The card has its own abstract/PDF buttons, so drop the duplicate link
        # the digest puts at the end of the meta line. Keep the arXiv id.
        meta = re.sub(r"\s*·\s*\[abs\]\([^)]*\)\s*$", "", e["meta"])
        why = f'<div class="why">{inline(e["why"])}</div>' if e["why"] else ""
        cards.append(f"""
<article class="card{' picked' if pid in picks else ''}" id="p{pid or num}">
  <div class="rank">{html.escape(num)}</div>
  <h3>{inline(title)}</h3>
  <div class="meta">{inline(meta)}</div>
  {why}
  {blocks_to_html(e["body"])}
  <div class="actions">{links}{pick_button(pid, pid in picks)}</div>
</article>""")
    return "\n".join(cards)


def render_also(lines, picks):
    items = []
    for line in lines:
        s = line.strip()
        if not s.startswith("- "):
            continue
        pid = first_id(s)
        body = re.sub(r"^`arXiv:[^`]*`\s*", "", s[2:])
        links = ""
        if pid:
            links = (f'<a class="btn sm" href="https://arxiv.org/abs/{pid}" '
                     f'target="_blank" rel="noopener">abs</a>'
                     f'<a class="btn sm" href="https://arxiv.org/pdf/{pid}" '
                     f'target="_blank" rel="noopener">pdf</a>')
        items.append(f"""
<div class="also{' picked' if pid in picks else ''}">
  <div class="also-body">{inline(body)}</div>
  <div class="actions">{links}{pick_button(pid, pid in picks)}</div>
</div>""")
    return "\n".join(items)


def render_digest(date, picks):
    md = (HOME / "digests" / f"{date}.md").read_text(encoding="utf-8")
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)

    sections, cur = [], ("", [])
    for line in md.split("\n"):
        if line.startswith("## "):
            sections.append(cur)
            cur = (line[3:].strip(), [])
        elif line.startswith("# "):
            continue
        else:
            cur[1].append(line)
    sections.append(cur)

    parts = []
    for name, lines in sections:
        if not name:
            body = blocks_to_html(lines)
            if body.strip():
                parts.append(f'<div class="lede">{body}</div>')
            continue
        low = name.lower()
        if "five" in low or "tonight" in low and "trend" not in low:
            inner = (EXPLAINER + render_five(lines, picks))
        elif "also worth" in low:
            inner = render_also(lines, picks)
        else:
            inner = blocks_to_html(lines)
        parts.append(f'<section><h2>{inline(name)}</h2>{inner}</section>')
    return "\n".join(parts)


# ==========================================================================
#  the page
# ==========================================================================

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1c1a;--dim:#6b675f;--line:#e2ded4;--card:#fff;
--accent:#8a5a2b;--good:#2f6f4f;--goodbg:#eef6f1;--code:#f2efe8}
@media(prefers-color-scheme:dark){:root{--bg:#16161a;--fg:#e8e6e1;--dim:#9b968c;
--line:#2c2c33;--card:#1d1d22;--accent:#d8a56a;--good:#7fc4a0;--goodbg:#1b2a23;
--code:#26262c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:17px/1.62 Charter,Georgia,'Iowan Old Style',serif;
-webkit-font-smoothing:antialiased}
header{position:sticky;top:0;z-index:10;background:color-mix(in srgb,var(--bg) 88%,transparent);
backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.bar{max-width:860px;margin:0 auto;padding:14px 24px;display:flex;
align-items:baseline;gap:14px;flex-wrap:wrap}
.bar h1{font-size:19px;margin:0;letter-spacing:-.01em}
.bar .date{color:var(--dim);font-size:15px}
.bar .sp{flex:1}
main{max-width:860px;margin:0 auto;padding:8px 24px 120px}
h2{font-size:15px;text-transform:uppercase;letter-spacing:.09em;
color:var(--dim);font-weight:600;margin:52px 0 18px;
font-family:-apple-system,system-ui,sans-serif}
h3{font-size:21px;line-height:1.3;margin:2px 0 8px;letter-spacing:-.01em}
h4{font-size:17px;margin:22px 0 6px}
.lede{font-size:19px;color:var(--fg);margin:22px 0 8px}
.card{position:relative;background:var(--card);border:1px solid var(--line);
border-radius:12px;padding:22px 24px 18px 58px;margin:0 0 18px}
.card.picked{border-color:var(--good);background:var(--goodbg)}
.rank{position:absolute;left:20px;top:22px;font-size:22px;color:var(--accent);
font-weight:600;font-family:-apple-system,system-ui,sans-serif}
.meta{color:var(--dim);font-size:14.5px;margin-bottom:12px;
font-family:-apple-system,system-ui,sans-serif}
.why{border-left:3px solid var(--accent);padding:2px 0 2px 13px;margin:0 0 14px;
color:var(--fg)}
ul{margin:10px 0;padding-left:22px}li{margin:7px 0}
code{background:var(--code);padding:1px 5px;border-radius:4px;
font:13.5px ui-monospace,SFMono-Regular,Menlo,monospace}
a{color:var(--accent)}
.actions{display:flex;gap:8px;align-items:center;margin-top:16px;flex-wrap:wrap;
font-family:-apple-system,system-ui,sans-serif}
.btn{display:inline-block;font-size:13.5px;text-decoration:none;color:var(--fg);
border:1px solid var(--line);background:var(--bg);padding:5px 12px;
border-radius:999px}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn.sm{font-size:12.5px;padding:3px 10px}
.pick{margin-left:auto;font:600 13.5px -apple-system,system-ui,sans-serif;
cursor:pointer;border:1px solid var(--line);background:var(--bg);
color:var(--dim);padding:5px 14px;border-radius:999px;display:flex;gap:6px}
.pick:hover{border-color:var(--good);color:var(--good)}
.pick .tick{opacity:.25}
.pick.on{background:var(--good);border-color:var(--good);color:#fff}
.pick.on .tick{opacity:1}
.also{border-bottom:1px solid var(--line);padding:16px 0}
.also:last-child{border-bottom:none}
.also.picked{background:var(--goodbg);border-radius:10px;padding:16px 16px;
margin:4px -16px}
.also .actions{margin-top:10px}
.status{max-width:860px;margin:40px auto;padding:0 24px}
.spin{display:inline-block;width:13px;height:13px;border:2px solid var(--line);
border-top-color:var(--accent);border-radius:50%;animation:s .8s linear infinite;
vertical-align:-2px;margin-right:9px}
@keyframes s{to{transform:rotate(360deg)}}
.log{font:12.5px ui-monospace,Menlo,monospace;color:var(--dim);
background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:14px 16px;margin-top:18px;white-space:pre-wrap;max-height:280px;
overflow:auto}
footer{position:fixed;bottom:0;left:0;right:0;background:var(--card);
border-top:1px solid var(--line);font:14px -apple-system,system-ui,sans-serif}
.fbar{max-width:860px;margin:0 auto;padding:12px 24px;display:flex;gap:14px;
align-items:center;flex-wrap:wrap}
.fbar .sp{flex:1}
button.act{font:14px -apple-system,system-ui,sans-serif;cursor:pointer;
border:1px solid var(--line);background:var(--bg);color:var(--fg);
padding:6px 14px;border-radius:8px}
button.act:hover{border-color:var(--accent);color:var(--accent)}
.count{color:var(--dim)}
.toast{position:fixed;bottom:74px;left:50%;transform:translateX(-50%);
background:var(--fg);color:var(--bg);padding:9px 18px;border-radius:999px;
font:14px -apple-system,system-ui,sans-serif;opacity:0;pointer-events:none;
transition:opacity .2s;z-index:20}
.toast.show{opacity:.95}
.explain{font:14.5px/1.55 -apple-system,system-ui,sans-serif;color:var(--dim);
background:var(--card);border:1px solid var(--line);border-left:3px solid var(--accent);
border-radius:10px;padding:12px 16px;margin:0 0 20px}
.explain b{color:var(--fg)}
.lookup{display:flex;gap:10px;margin:24px 0}
.lookup input{flex:1;font:16px -apple-system,system-ui,sans-serif;padding:10px 14px;
border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--fg)}
.lookup input:focus{outline:none;border-color:var(--accent)}
.match{border:1px solid var(--line);background:var(--card);border-radius:10px;
padding:16px 18px;margin:0 0 12px}
.match.picked{border-color:var(--good);background:var(--goodbg)}
.match .actions{margin-top:12px}
.checks{display:flex;flex-direction:column;gap:2px;margin:18px 0;
font:15px -apple-system,system-ui,sans-serif}
.chk{display:flex;align-items:baseline;gap:9px;padding:5px 8px;border-radius:7px;
cursor:pointer}
.chk:hover{background:var(--card)}
.chk b{font-family:ui-monospace,Menlo,monospace;font-size:13px;min-width:150px;
color:var(--accent)}
.chk span{color:var(--dim)}
.chk.sub{margin-left:26px}
.chk.sub b{font-size:12.5px;min-width:170px;color:var(--dim)}
.arch{border-bottom:1px solid var(--line);padding:2px 0}
.arch:last-child{border-bottom:none}
.arch details{margin:0 0 6px 26px}
.arch summary{cursor:pointer;color:var(--dim);font-size:12.5px;
list-style:none;padding:2px 0}
.arch summary::-webkit-details-marker{display:none}
.arch summary:before{content:"▸ ";color:var(--accent)}
.arch details[open] summary:before{content:"▾ "}
.checks{max-height:520px;overflow-y:auto;border:1px solid var(--line);
border-radius:10px;padding:10px 14px;background:var(--card)}
button.act.big{font-size:16px;padding:11px 22px;border-color:var(--accent);
color:var(--accent);font-weight:600}
section{margin-bottom:8px}
select{font:14px -apple-system,system-ui,sans-serif;background:var(--bg);
color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:5px 8px}
"""

JS = """
async function post(url,body){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body||{})});
  return r.json();
}
function toast(msg){
  let t=document.querySelector('.toast');
  if(!t){t=document.createElement('div');t.className='toast';document.body.append(t);}
  t.textContent=msg;t.classList.add('show');
  clearTimeout(t._h);t._h=setTimeout(()=>t.classList.remove('show'),2600);
}
document.addEventListener('click',async e=>{
  const b=e.target.closest('.pick');
  if(b){
    const on=b.classList.contains('on');
    b.disabled=true;
    const res=await post('/api/pick',{id:b.dataset.id,picked:!on});
    b.disabled=false;
    if(res.ok){
      b.classList.toggle('on',!on);
      b.setAttribute('aria-pressed',String(!on));
      b.querySelector('.lbl').textContent=!on?window.__pickOn:window.__pickOff;
      const box=b.closest('.card,.also'); if(box) box.classList.toggle('picked',!on);
      const c=document.getElementById('count');
      if(c&&typeof res.total==='number'){
        const n=res.total;
        c.textContent = n===0 ? 'no papers marked yet'
          : n<12 ? n+' marked · about '+(12-n)+' more before retuning helps'
          : n+' marked · enough to retune';
      }
      toast(!on ? (res.saved ? 'Recorded — '+res.saved : 'Recorded. Click again to undo.')
                : 'Taken back.');
    } else { toast(res.error||'Could not save'); }
    return;
  }
  const a=e.target.closest('[data-act]');
  if(!a) return;
  const act=a.dataset.act;
  if(act==='rebuild'){ location.href='/?rebuild=1'; }
  if(act==='learn'){
    a.disabled=true; a.textContent='Retuning...';
    const res=await post('/api/learn',{});
    if(res.started){ pollLearn(a); } else { a.disabled=false; toast('Already running'); }
  }
});
async function pollLearn(btn){
  const r=await (await fetch('/api/status')).json();
  if(r.learn==='running'){ setTimeout(()=>pollLearn(btn),1500); return; }
  btn.disabled=false; btn.textContent='Retune from my picks';
  toast(r.learn_message||'Done');
}
document.getElementById('hist')?.addEventListener('change',e=>{
  if(e.target.value) location.href='/?date='+e.target.value;
});
if(window.__building){
  setInterval(async()=>{
    const r=await (await fetch('/api/status')).json();
    const el=document.getElementById('msg'); if(el) el.textContent=r.message||'';
    const lg=document.getElementById('log');
    if(lg&&r.lines) lg.textContent=r.lines.join('\\n');
    if(r.phase==='ready'||r.phase==='uptodate'||r.phase==='error') location.href='/';
  },1500);
}
"""

MATHJAX = """
<script>window.MathJax={tex:{inlineMath:[['$','$']],displayMath:[['$$','$$']]},
options:{ignoreHtmlClass:'meta|actions'},startup:{typeset:true}};</script>
<script async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"
 onerror="void 0"></script>
"""


PROFILE_PAGE = """
<header><div class="bar">
  <h1>Tune from a researcher</h1>
  <span class="sp"></span>
  <a class="btn" href="/">back to the digest</a>
</div></header>
<main>
<p class="explain">Give a name and this reads that person's actual publication
record, then offers their frequent coauthors and the recurring phrases from
their own paper titles as things to track. Better than guessing a list by hand.
<br><br>
Data comes from <b>OpenAlex</b>, which covers essentially all of the physics
literature. Google Scholar has no API and blocks automated access, so a Scholar
link can't be read directly — use the name and you get the same corpus.</p>

<div id="people"></div>
<div class="actions">
  <button class="act" id="addrow">+ add another person</button>
  <button class="act" id="useall" disabled>Use these people</button>
</div>
<div id="out"></div>
</main>
<script>
const MAXP = 3;
const chosen = [];            // {who, at, pick, label}
const peopleEl = document.getElementById('people');
const outEl = document.getElementById('out');
function jpost(u,b){return fetch(u,{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})})
  .then(r=>r.json());}
function busy(m){outEl.innerHTML='<p class="explain"><span class="spin"></span>'+m+'</p>';}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');}

function addRow(){
  if(peopleEl.children.length>=MAXP) return;
  const i=peopleEl.children.length;
  const row=document.createElement('div');
  row.className='prow'; row.dataset.i=i;
  row.innerHTML=
    '<div class="lookup">'+
      '<input class="who" placeholder="researcher name'+(i?' (optional)':'')+'">'+
      '<input class="at" placeholder="university (optional, but fixes common names)">'+
      '<button class="act find">Find</button>'+
    '</div><div class="cand"></div>';
  peopleEl.append(row);
  syncButtons();
}
function syncButtons(){
  document.getElementById('addrow').disabled = peopleEl.children.length>=MAXP;
  document.getElementById('useall').disabled = chosen.filter(Boolean).length===0;
  const n=chosen.filter(Boolean).length;
  document.getElementById('useall').textContent =
    n>1 ? 'Use these '+n+' people (weight their overlap)' : 'Use this person';
}
peopleEl.addEventListener('click', async e=>{
  const row=e.target.closest('.prow'); if(!row) return;
  const i=+row.dataset.i, cand=row.querySelector('.cand');
  if(e.target.classList.contains('find')){
    const who=row.querySelector('.who').value.trim();
    const at=row.querySelector('.at').value.trim();
    if(!who) return;
    cand.innerHTML='<p class="explain"><span class="spin"></span>Searching...</p>';
    const r=await jpost('/api/profile/search',{who,at});
    if(!r.ok){cand.innerHTML='<p class="explain">'+esc(r.error)+'</p>';return;}
    if(!r.matches.length){
      cand.innerHTML='<p class="explain">Nobody found'+(at?' at '+esc(at):'')+
        '. Try the full institution name, or clear the university box.</p>';return;}
    cand.innerHTML=r.matches.map((m,k)=>{
      let where=esc(m.institution||'unknown');
      if(m.via_institution&&m.last_known&&m.last_known!==m.institution)
        where='affiliated with '+where+'; now listed at '+esc(m.last_known);
      return '<div class="match"><b>'+esc(m.name)+'</b>'+
        (m.exact?' <span class="tag">exact name</span>':'')+
        '<div class="meta">'+where+'</div>'+
        '<div class="meta">'+m.works+' papers · '+m.cited.toLocaleString()+' citations</div>'+
        '<div class="meta">'+esc((m.topics||[]).slice(0,3).join(' · '))+'</div>'+
        '<div class="actions"><button class="act use" data-k="'+k+'">Use</button></div></div>';
    }).join('');
    return;
  }
  if(e.target.classList.contains('use')){
    const k=+e.target.dataset.k;
    const who=row.querySelector('.who').value.trim();
    const at=row.querySelector('.at').value.trim();
    const label=e.target.closest('.match').querySelector('b').textContent;
    chosen[i]={who,at,pick:k,label};
    cand.innerHTML='<div class="match picked"><b>'+esc(label)+'</b> — selected'+
      '<div class="actions"><button class="act clear">change</button></div></div>';
    syncButtons();
    return;
  }
  if(e.target.classList.contains('clear')){
    chosen[i]=null; cand.innerHTML=''; syncButtons(); return;
  }
});
document.getElementById('addrow').addEventListener('click',addRow);
document.getElementById('useall').addEventListener('click', async e=>{
  const people=chosen.filter(Boolean);
  if(!people.length) return;
  e.target.disabled=true;
  busy('Reading '+(people.length>1?people.length+' publication records':'the publication record')+
       ' — up to a minute'+(people.length>1?' each':'')+'...');
  const r=await jpost('/api/profile/apply',{people});
  e.target.disabled=false;
  outEl.innerHTML='<p class="explain">'+(r.ok
    ? 'Added'+(people.length>1
        ? ' — phrases that appear in more than one of these corpora are weighted highest, so the digest favours what they have in common.'
        : ' their coauthors and topics to your config.')+
      ' You can prune the list later in config.toml.'
    : esc(r.error))+'</p>';
});
addRow();

</script>
"""


def section_picker(current):
    """Every arXiv archive, subcategories folded away until you open one."""
    out = []
    for code, name, subs in ARXIV_TAXONOMY:
        on = code in current
        any_sub = any(s in current for s, _ in subs)
        row = (f'<label class="chk"><input type="checkbox" value="{code}"'
               f'{" checked" if on else ""}> <b>{code}</b>'
               f'<span>{html.escape(name)}</span></label>')
        if not subs:
            out.append(f'<div class="arch">{row}</div>')
            continue
        sub_rows = "".join(
            f'<label class="chk sub"><input type="checkbox" value="{s}"'
            f'{" checked" if s in current else ""}> <b>{s}</b>'
            f'<span>{html.escape(sn)}</span></label>' for s, sn in subs)
        out.append(
            f'<div class="arch">{row}'
            f'<details{" open" if any_sub else ""}>'
            f'<summary>{len(subs)} subsections</summary>{sub_rows}</details>'
            f'</div>')
    return "".join(out)


def setup_page():
    boxes = [section_picker(set(read_feeds()))]
    n_sections = len(ARXIV_TAXONOMY) + sum(len(s) for _, _, s in ARXIV_TAXONOMY)
    dl = pick.download_dir()
    current_dl = html.escape(str(dl)) if dl else ''

    return page("Set up your digest", f"""
<header><div class="bar"><h1>arXiv digest — setup</h1>
<span class="sp"></span><span class="date">three steps, then it runs</span>
</div></header>
<main>
<p class="explain">This reads the arXiv sections you choose every weeknight and
writes you a short digest of the few papers worth downloading. Set it up once.</p>

<section><h2>Step 1 · Sign in</h2>
<p class="explain">The digest needs a model to read the abstracts, and it uses a
subscription you already pay for — <b>no API key, nothing extra to buy</b>.
Install whichever matches your plan, run it once to sign in, then press Check.
<br><br>
<b>ChatGPT Plus / Pro:</b> <code>npm install -g @openai/codex</code> then run
<code>codex</code> and choose "Sign in with ChatGPT".
<br>
<b>Claude Pro / Max:</b> <code>curl -fsSL https://claude.ai/install.sh | bash</code>
then run <code>claude</code> and sign in.
<br><br>
Sign in with the <i>subscription</i>, not an API key — an API key bills
separately per use.</p>
<div class="actions"><button class="act" id="chk">Check my sign-in</button></div>
<div id="auth"></div>
</section>

<section><h2>Step 2 · Whose work should it follow?</h2>
<p class="explain">Give a researcher's name — yours, or whoever's interests match
what you want to read. It reads their actual publication record and tracks their
frequent coauthors and recurring topics. You can skip this and use the general
defaults instead.
<br><br>
<i>Google Scholar has no API and blocks automated access, so paste the name
rather than a Scholar link — the corpus comes from OpenAlex, which covers the
same literature.</i></p>
<div id="people"></div>
<div class="actions">
  <button class="act" id="addrow">+ add another person</button>
  <button class="act" id="useall" disabled>Use these people</button>
</div>
<div id="out"></div>
</section>

<section><h2>Step 3 · Which arXiv sections?</h2>
<p class="explain">Tick a whole archive, or open it and pick individual
subsections. Ticking an archive already covers everything inside it, so don't
tick both. Physics archives are listed first, but everything arXiv publishes is
here — <b>{n_sections}</b> sections in all.</p>
<div class="checks">{''.join(boxes)}</div>
<div class="actions"><button class="act" id="savefeeds">Save sections</button>
<span id="feedmsg" class="count"></span></div>
</section>

<section><h2>Step 4 · Where should papers be saved?</h2>
<p class="explain">When you click <b>Want this</b> on a paper, the PDF is
downloaded here. Leave it empty and nothing is downloaded — you just get the
links, and clicking <b>PDF</b> opens arXiv in your browser.
<br><br>
<b>Zotero users:</b> point this at a folder and Zotero can pick papers up from
it automatically. Direct "send to Zotero" is <i>coming soon</i>; for now, in
Zotero use <i>Settings → Advanced → Files and Folders</i>, or just drag the
folder into a collection.</p>
<div class="lookup">
  <input id="dldir" type="text" placeholder="~/Papers/arxiv  (leave empty for links only)" value="{current_dl}">
  <button class="act" id="savedl">Save folder</button>
</div>
<span id="dlmsg" class="count"></span>
</section>

<section><h2>Done?</h2>
<p class="explain">Building the first digest takes a couple of minutes — it reads
every abstract in tonight's listing.</p>
<div class="actions"><button class="act big" id="finish">Build my first digest</button></div>
</section>
</main>
<script>
async function jpost(u,b){{const r=await fetch(u,{{method:'POST',
  headers:{{'Content-Type':'application/json'}},body:JSON.stringify(b||{{}})}});
  return r.json();}}
const auth=document.getElementById('auth');
document.getElementById('chk').addEventListener('click',async e=>{{
  e.target.disabled=true;
  auth.innerHTML='<p class="explain"><span class="spin"></span>Asking each CLI to reply — up to a minute...</p>';
  const r=await jpost('/api/setup/auth',{{}});
  e.target.disabled=false; e.target.textContent='Check again';
  let h='';
  for(const k of ['claude','codex']){{
    const s=r[k], name=k==='claude'?'Claude Code':'Codex CLI';
    if(!s.installed) h+='<div class="match"><b>'+name+'</b> — not installed</div>';
    else if(s.signed_in) h+='<div class="match picked"><b>'+name+'</b> — signed in and working ✓</div>';
    else h+='<div class="match"><b>'+name+'</b> — installed but not usable yet'+
      '<div class="meta">'+s.detail.replace(/</g,'&lt;')+'</div></div>';
  }}
  h+= r.ready ? '<p class="explain"><b>Ready.</b> '+r.using+' will be used.</p>'
             : '<p class="explain">Nothing usable yet. Install one above, run it once to sign in, then press Check again.</p>';
  auth.innerHTML=h;
}});

const MAXP = 3;
const chosen = [];            // {{who, at, pick, label}}
const peopleEl = document.getElementById('people');
const outEl = document.getElementById('out');
function jpost(u,b){{return fetch(u,{{method:'POST',
  headers:{{'Content-Type':'application/json'}},body:JSON.stringify(b||{{}})}})
  .then(r=>r.json());}}
function busy(m){{outEl.innerHTML='<p class="explain"><span class="spin"></span>'+m+'</p>';}}
function esc(s){{return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');}}

function addRow(){{
  if(peopleEl.children.length>=MAXP) return;
  const i=peopleEl.children.length;
  const row=document.createElement('div');
  row.className='prow'; row.dataset.i=i;
  row.innerHTML=
    '<div class="lookup">'+
      '<input class="who" placeholder="researcher name'+(i?' (optional)':'')+'">'+
      '<input class="at" placeholder="university (optional, but fixes common names)">'+
      '<button class="act find">Find</button>'+
    '</div><div class="cand"></div>';
  peopleEl.append(row);
  syncButtons();
}}
function syncButtons(){{
  document.getElementById('addrow').disabled = peopleEl.children.length>=MAXP;
  document.getElementById('useall').disabled = chosen.filter(Boolean).length===0;
  const n=chosen.filter(Boolean).length;
  document.getElementById('useall').textContent =
    n>1 ? 'Use these '+n+' people (weight their overlap)' : 'Use this person';
}}
peopleEl.addEventListener('click', async e=>{{
  const row=e.target.closest('.prow'); if(!row) return;
  const i=+row.dataset.i, cand=row.querySelector('.cand');
  if(e.target.classList.contains('find')){{
    const who=row.querySelector('.who').value.trim();
    const at=row.querySelector('.at').value.trim();
    if(!who) return;
    cand.innerHTML='<p class="explain"><span class="spin"></span>Searching...</p>';
    const r=await jpost('/api/profile/search',{{who,at}});
    if(!r.ok){{cand.innerHTML='<p class="explain">'+esc(r.error)+'</p>';return;}}
    if(!r.matches.length){{
      cand.innerHTML='<p class="explain">Nobody found'+(at?' at '+esc(at):'')+
        '. Try the full institution name, or clear the university box.</p>';return;}}
    cand.innerHTML=r.matches.map((m,k)=>{{
      let where=esc(m.institution||'unknown');
      if(m.via_institution&&m.last_known&&m.last_known!==m.institution)
        where='affiliated with '+where+'; now listed at '+esc(m.last_known);
      return '<div class="match"><b>'+esc(m.name)+'</b>'+
        (m.exact?' <span class="tag">exact name</span>':'')+
        '<div class="meta">'+where+'</div>'+
        '<div class="meta">'+m.works+' papers · '+m.cited.toLocaleString()+' citations</div>'+
        '<div class="meta">'+esc((m.topics||[]).slice(0,3).join(' · '))+'</div>'+
        '<div class="actions"><button class="act use" data-k="'+k+'">Use</button></div></div>';
    }}).join('');
    return;
  }}
  if(e.target.classList.contains('use')){{
    const k=+e.target.dataset.k;
    const who=row.querySelector('.who').value.trim();
    const at=row.querySelector('.at').value.trim();
    const label=e.target.closest('.match').querySelector('b').textContent;
    chosen[i]={{who,at,pick:k,label}};
    cand.innerHTML='<div class="match picked"><b>'+esc(label)+'</b> — selected'+
      '<div class="actions"><button class="act clear">change</button></div></div>';
    syncButtons();
    return;
  }}
  if(e.target.classList.contains('clear')){{
    chosen[i]=null; cand.innerHTML=''; syncButtons(); return;
  }}
}});
document.getElementById('addrow').addEventListener('click',addRow);
document.getElementById('useall').addEventListener('click', async e=>{{
  const people=chosen.filter(Boolean);
  if(!people.length) return;
  e.target.disabled=true;
  busy('Reading '+(people.length>1?people.length+' publication records':'the publication record')+
       ' — up to a minute'+(people.length>1?' each':'')+'...');
  const r=await jpost('/api/profile/apply',{{people}});
  e.target.disabled=false;
  outEl.innerHTML='<p class="explain">'+(r.ok
    ? 'Added'+(people.length>1
        ? ' — phrases that appear in more than one of these corpora are weighted highest, so the digest favours what they have in common.'
        : ' their coauthors and topics to your config.')+
      ' You can prune the list later in config.toml.'
    : esc(r.error))+'</p>';
}});
addRow();

document.getElementById('savefeeds').addEventListener('click',async ()=>{{
  const f=[...document.querySelectorAll('.checks input:checked')].map(i=>i.value);
  const r=await jpost('/api/setup/feeds',{{feeds:f}});
  document.getElementById('feedmsg').textContent =
    r.ok ? 'saved: '+r.feeds.join(', ') : r.error;
}});
document.getElementById('savedl').addEventListener('click',async ()=>{{
  const v=document.getElementById('dldir').value.trim();
  const r=await jpost('/api/setup/download-dir',{{path:v}});
  document.getElementById('dlmsg').textContent = r.ok
    ? (r.path ? 'papers will be saved to '+r.path : 'not saving PDFs — links only')
    : r.error;
}});
document.getElementById('finish').addEventListener('click',async e=>{{
  e.target.disabled=true; e.target.textContent='Starting...';
  await jpost('/api/setup/done',{{}});
  location.href='/?rebuild=1';
}});
</script>
""")


def page(title, body, building=False):
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>{CSS}</style></head><body>
{body}
<script>window.__building={'true' if building else 'false'};
window.__pickOn={json.dumps(PICK_ON)};window.__pickOff={json.dumps(PICK_OFF)};</script>
<script>{JS}</script>
{MATHJAX}
</body></html>"""


def status_page():
    with LOCK:
        st = dict(STATE)
    phase = st["phase"]
    if phase == "error":
        inner = (f'<h1>Something went wrong</h1><p>{html.escape(st["message"])}</p>'
                 f'<p><a class="btn" href="/?rebuild=1">Try again</a> '
                 f'<a class="btn" href="/?date=latest">Read the last digest</a></p>')
        if st["lines"]:
            inner += f'<div class="log">{html.escape(chr(10).join(st["lines"]))}</div>'
    else:
        inner = (f'<h1><span class="spin"></span>Building tonight\'s digest</h1>'
                 f'<p id="msg">{html.escape(st["message"])}</p>'
                 f'<p style="color:var(--dim)">Reading about 155 abstracts. '
                 f'This takes a couple of minutes — you can leave it.</p>'
                 f'<div class="log" id="log">'
                 f'{html.escape(chr(10).join(st["lines"]))}</div>')
    return page("arXiv digest", f'<div class="status">{inner}</div>',
                building=(phase not in ("error",)))


def digest_page(date):
    picks = pick.picked_ids()
    stats = run_stats(date)
    dates = digest_dates()
    try:
        body = render_digest(date, picks)
    except OSError as exc:
        return status_page() if not date else page(
            "arXiv digest", f'<div class="status"><h1>Cannot read {date}</h1>'
                            f'<p>{html.escape(str(exc))}</p></div>')

    pretty = datetime.strptime(date, "%Y-%m-%d").strftime("%A %-d %B %Y")
    sections = html.escape(", ".join(read_feeds()))
    tally = (f'{stats.get("total", 0)} papers announced · '
             f'{stats.get("new", 0)} new · '
             f'{stats.get("replacements", 0)} replacements'
             if stats else "")
    options = "".join(
        f'<option value="{d}"{" selected" if d == date else ""}>{d}</option>'
        for d in dates)
    npicks = len(picks)

    header = f"""<header><div class="bar">
  <h1>arXiv {sections}</h1>
  <span class="date">{html.escape(pretty)}</span>
  <span class="sp"></span>
  <span class="date">{tally}</span>
  {'<select id="hist" title="Earlier digests">' + options + '</select>'
   if len(dates) > 1 else ''}
</div></header>"""

    if npicks == 0:
        progress = "no papers marked yet"
    elif npicks < 12:
        progress = f"{npicks} marked · about {12 - npicks} more before retuning helps"
    else:
        progress = f"{npicks} marked · enough to retune"

    footer = f"""<footer><div class="fbar">
  <span class="count" id="count">{progress}</span>
  <span class="sp"></span>
  <a class="btn" href="/setup">Settings</a>
  <a class="btn" href="/profile">Tune from a researcher</a>
  <button class="act" data-act="learn">Retune from my picks</button>
  <button class="act" data-act="rebuild">Rebuild tonight</button>
</div></footer>"""

    return page(f"arXiv {date}", header + f"<main>{body}</main>" + footer)


# ==========================================================================
#  server
# ==========================================================================

class Handler(BaseHTTPRequestHandler):
    server_version = "arxivdigest/1.0"

    def log_message(self, *a):
        pass

    def _send(self, code, ctype, payload):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except BrokenPipeError:
            pass

    def _json(self, obj, code=200):
        self._send(code, "application/json; charset=utf-8",
                   json.dumps(obj).encode("utf-8"))

    def _html(self, text):
        self._send(200, "text/html; charset=utf-8", text.encode("utf-8"))

    def do_GET(self):
        set_state(last_seen=time.time())
        u = urlparse(self.path)
        q = parse_qs(u.query)

        if u.path == "/api/status":
            with LOCK:
                st = dict(STATE)
            st.pop("last_seen", None)
            return self._json(st)

        if u.path == "/api/ping":
            return self._json({"ok": True})

        if u.path == "/profile":
            return self._html(page("Tune from a researcher", PROFILE_PAGE))

        if u.path == "/setup":
            return self._html(setup_page())

        if u.path != "/":
            return self._send(404, "text/plain; charset=utf-8", b"not found")

        # A brand-new install goes to the wizard rather than straight to a build.
        if not SETUP_DONE.exists() and not digest_dates() and not q.get("rebuild"):
            return self._html(setup_page())

        if q.get("rebuild"):
            with LOCK:
                busy = STATE["phase"] == "building"
            if not busy:
                threading.Thread(target=build_thread, args=(True,),
                                 daemon=True).start()
                time.sleep(0.4)
            return self._html(status_page())

        with LOCK:
            phase = STATE["phase"]
        if phase == "building":
            return self._html(status_page())

        want = q.get("date", [None])[0]
        dates = digest_dates()
        if want in (None, "latest") or want not in dates:
            want = dates[0] if dates else None
        if not want:
            return self._html(status_page())
        return self._html(digest_page(want))

    def do_POST(self):
        set_state(last_seen=time.time())
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json({"ok": False, "error": "bad request"}, 400)

        if u.path == "/api/pick":
            pid = pick.normalise(data.get("id"))
            if not pid:
                return self._json({"ok": False, "error": "no id"}, 400)
            if data.get("picked"):
                added, skipped, unknown = pick.record_ids([pid])
                if unknown:
                    return self._json({"ok": False,
                                       "error": "not in any stored run"})
                rec = pick.existing_picks().get(pid, {})
                saved = rec.get("saved_to", "")
                where = ("saved to " + str(Path(saved).parent) if saved
                         else "" if pick.download_dir() is None
                         else "could not download the PDF")
            else:
                pick.unrecord_ids([pid])
                where = ""
            return self._json({"ok": True, "total": len(pick.picked_ids()),
                               "saved": where})

        if u.path == "/api/setup/auth":
            claude = probe_engine("claude")
            codex = probe_engine("codex")
            using = ("Claude Code" if claude["signed_in"]
                     else "Codex CLI" if codex["signed_in"] else "")
            return self._json({"claude": claude, "codex": codex,
                               "ready": bool(using), "using": using})

        if u.path == "/api/setup/feeds":
            ok, res = write_feeds(data.get("feeds") or [])
            return self._json({"ok": True, "feeds": res} if ok
                              else {"ok": False, "error": res})

        if u.path == "/api/setup/download-dir":
            ok, res = write_download_dir(data.get("path"))
            return self._json({"ok": True, "path": res} if ok
                              else {"ok": False, "error": res})

        if u.path == "/api/setup/done":
            SETUP_DONE.write_text("setup completed\n", encoding="utf-8")
            return self._json({"ok": True})

        if u.path.startswith("/api/profile/"):
            what = u.path.rsplit("/", 1)[-1]

            if what == "search":
                who = str(data.get("who") or "").strip()
                if not who:
                    return self._json({"ok": False, "error": "give a name"}, 400)
                cli = ["--list", "--json", who]
                at = str(data.get("at") or "").strip()
                if at:
                    cli += ["--at", at]
                ok, res = run_profile(cli, timeout=90)
                if not ok:
                    return self._json({"ok": False, "error": res})
                # --list --json returns one match list per name; we sent one.
                return self._json({"ok": True, "matches": res[0] if res else []})

            # build / apply take a list of up to three people
            people = data.get("people") or []
            if not people:
                return self._json({"ok": False, "error": "no one selected"}, 400)
            cli = []
            for p in people[:3]:
                cli.append(str(p.get("who") or "").strip())
            if not all(cli):
                return self._json({"ok": False, "error": "a name is blank"}, 400)
            for p in people[:3]:
                cli += ["--at", str(p.get("at") or "")]
                cli += ["--pick", str(int(p.get("pick") or 0))]

            if what == "build":
                ok, res = run_profile(cli + ["--json"], timeout=900)
                if not ok:
                    return self._json({"ok": False, "error": res})
                return self._json({"ok": True, "works_seen": res["works_seen"],
                                   "coauthors": res["coauthors"],
                                   "terms": res["terms"],
                                   "shared_terms": res.get("shared_terms", []),
                                   "toml": res["toml"]})
            if what == "apply":
                ok, res = run_profile(cli + ["--apply"], timeout=900)
                return self._json({"ok": True, "message": res} if ok
                                  else {"ok": False, "error": res})
            return self._json({"ok": False, "error": "unknown action"}, 404)

        if u.path == "/api/learn":
            with LOCK:
                busy = STATE["learn"] == "running"
            if busy:
                return self._json({"started": False})
            threading.Thread(target=learn_thread, daemon=True).start()
            return self._json({"started": True})

        return self._json({"ok": False, "error": "not found"}, 404)


def already_running():
    """If a previous instance is alive, return its port."""
    if not PORT_FILE.exists():
        return None
    try:
        port = int(PORT_FILE.read_text().strip())
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as r:
            if json.loads(r.read()).get("ok"):
                return port
    except Exception:
        return None
    return None


def idle_watchdog(server):
    while True:
        time.sleep(60)
        with LOCK:
            quiet = time.time() - STATE["last_seen"]
            busy = STATE["phase"] == "building" or STATE["learn"] == "running"
        if quiet > IDLE_TIMEOUT and not busy:
            server.shutdown()
            return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()

    bootstrap_config()

    running = already_running()
    if running:
        if not args.no_open:
            webbrowser.open(f"http://127.0.0.1:{running}/")
        print(f"Reader already running at http://127.0.0.1:{running}/")
        return 0

    if sys.version_info < (3, 11):
        print("This needs Python 3.11 or newer (for tomllib).", file=sys.stderr)
        return 1

    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        print(f"Could not start the local server: {exc}", file=sys.stderr)
        return 1
    port = server.server_address[1]
    PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    PORT_FILE.write_text(str(port))

    fresh = not SETUP_DONE.exists() and not digest_dates()
    if args.no_build or fresh:
        # On a brand-new install, show the wizard instead of burning a couple of
        # minutes on a build the user has not configured yet.
        set_state(phase="ready", date=latest_date(), message="")
    else:
        threading.Thread(target=build_thread, args=(args.force,),
                         daemon=True).start()

    threading.Thread(target=idle_watchdog, args=(server,), daemon=True).start()

    url = f"http://127.0.0.1:{port}/"
    print(f"Reader at {url}   (quits by itself when left idle)")
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        PORT_FILE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
