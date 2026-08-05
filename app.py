#!/usr/bin/env python3
"""
The reader app: a small local web page for the nightly cond-mat digest.

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
import os
import re
import shutil
import socket
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
    """A GUI-launched app inherits almost no PATH. Rebuild a usable one."""
    env = dict(os.environ)
    extra = [str(Path.home() / ".local/bin"), "/usr/local/bin",
             "/opt/homebrew/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    parts = [p for p in env.get("PATH", "").split(":") if p]
    for p in extra:
        if p not in parts:
            parts.append(p)
    env["PATH"] = ":".join(parts)
    for var, exe in (("CLAUDE_BIN", "claude"), ("CODEX_BIN", "codex")):
        if not env.get(var):
            found = shutil.which(exe, path=env["PATH"])
            if found:
                env[var] = found
    # We already know we are a new enough Python; hand run.sh the same one
    # rather than letting it resolve a bare `python3` off a minimal PATH.
    env["PYTHON"] = sys.executable
    return env


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

    cmd = ["/bin/bash", str(HOME / "run.sh")] + (["--force"] if force else [])
    try:
        proc = subprocess.Popen(cmd, cwd=str(HOME), env=env,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                bufsize=1)
    except OSError as exc:
        set_state(phase="error", message=f"Could not start run.sh: {exc}")
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
                          f"run.sh exited with status {proc.returncode}.")


def learn_thread():
    set_state(learn="running", learn_message="Reading your picks...")
    env = resolve_env()
    try:
        proc = subprocess.run(["/bin/bash", str(HOME / "learn.sh")],
                              cwd=str(HOME), env=env, capture_output=True,
                              text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired) as exc:
        set_state(learn="error", learn_message=f"learn.sh failed: {exc}")
        return
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        set_state(learn="error", learn_message=out.strip()[-600:] or "failed")
        return
    if "No picks recorded yet" in out:
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

# The arXiv sections offered in the setup wizard. Picking a whole archive
# (e.g. "cond-mat") already includes every subsection under it.
ARXIV_SECTIONS = [
    ("cond-mat", "Condensed matter — the whole archive", True),
    ("cond-mat.soft", "  Soft condensed matter", False),
    ("cond-mat.str-el", "  Strongly correlated electrons", False),
    ("cond-mat.stat-mech", "  Statistical mechanics", False),
    ("cond-mat.supr-con", "  Superconductivity", False),
    ("cond-mat.mes-hall", "  Mesoscale and nanoscale physics", False),
    ("cond-mat.quant-gas", "  Quantum gases", False),
    ("cond-mat.dis-nn", "  Disordered systems and neural networks", False),
    ("cond-mat.mtrl-sci", "  Materials science", False),
    ("hep-th", "High energy physics — theory", False),
    ("quant-ph", "Quantum physics", False),
    ("math-ph", "Mathematical physics", False),
    ("gr-qc", "General relativity and quantum cosmology", False),
    ("physics.bio-ph", "Biological physics", False),
    ("physics.flu-dyn", "Fluid dynamics", False),
    ("nlin.AO", "Adaptation and self-organizing systems", False),
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
    cfg = HOME / "config.toml"
    if not cfg.exists():
        return ["cond-mat"]
    m = re.search(r"^\s*feeds\s*=\s*\[([^\]]*)\]",
                  cfg.read_text(encoding="utf-8"), re.M)
    if not m:
        return ["cond-mat"]
    return re.findall(r'"([^"]+)"', m.group(1)) or ["cond-mat"]


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
      toast(!on?'Recorded. Click again to undo.':'Taken back.');
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

<div class="lookup">
  <input id="who" type="text" placeholder="e.g. Leo Radzihovsky" autofocus>
  <button class="act" id="go">Look up</button>
</div>
<div id="out"></div>
</main>
<script>
const out=document.getElementById('out');
const who=document.getElementById('who');
function busy(msg){out.innerHTML='<p class="explain"><span class="spin"></span>'+msg+'</p>';}
async function jpost(url,body){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)});
  return r.json();
}
async function search(){
  const name=who.value.trim(); if(!name) return;
  busy('Searching...');
  const res=await jpost('/api/profile/search',{who:name});
  if(!res.ok){out.innerHTML='<p class="explain">'+res.error+'</p>';return;}
  if(!res.matches.length){out.innerHTML='<p class="explain">Nobody found.</p>';return;}
  out.innerHTML='<h2>Which one?</h2>'+res.matches.map((m,i)=>
    '<div class="match"><b>'+m.name+'</b> — '+(m.institution||'affiliation unknown')+
    '<div class="meta">'+m.works+' papers · '+m.cited.toLocaleString()+' citations</div>'+
    '<div class="meta">'+(m.topics||[]).slice(0,4).join(' · ')+'</div>'+
    '<div class="actions"><button class="act" data-pick="'+i+'">Read this corpus</button></div></div>'
  ).join('');
}
document.getElementById('go').addEventListener('click',search);
who.addEventListener('keydown',e=>{if(e.key==='Enter')search();});
out.addEventListener('click',async e=>{
  const p=e.target.closest('[data-pick]');
  if(p){
    busy('Reading the corpus — this takes up to a minute...');
    const res=await jpost('/api/profile/build',{who:who.value.trim(),pick:+p.dataset.pick});
    if(!res.ok){out.innerHTML='<p class="explain">'+res.error+'</p>';return;}
    window.__pick=+p.dataset.pick;
    out.innerHTML='<h2>Found in '+res.works_seen+' papers</h2>'+
      '<p class="explain"><b>'+res.coauthors.length+' frequent coauthors</b> and <b>'+
      res.terms.length+' recurring phrases</b>. Review below, then add them.</p>'+
      '<div class="actions"><button class="act" id="apply">Add all of this to my config</button></div>'+
      '<pre class="log" style="max-height:520px">'+
      res.toml.replace(/&/g,'&amp;').replace(/</g,'&lt;')+'</pre>';
    return;
  }
  if(e.target.id==='apply'){
    e.target.disabled=true; e.target.textContent='Adding...';
    const res=await jpost('/api/profile/apply',{who:who.value.trim(),pick:window.__pick||0});
    out.insertAdjacentHTML('afterbegin','<p class="explain">'+
      (res.ok?'Added. A backup of the old config is in config.toml.backup. '+
              'It takes effect on the next digest.':res.error)+'</p>');
    e.target.textContent='Added';
  }
});
</script>
"""


def setup_page():
    boxes = []
    current = set(read_feeds())
    for code, label, default in ARXIV_SECTIONS:
        on = code in current if current else default
        indent = ' style="margin-left:22px"' if label.startswith("  ") else ""
        boxes.append(
            f'<label class="chk"{indent}><input type="checkbox" value="{code}"'
            f'{" checked" if on else ""}> <b>{code}</b>'
            f'<span>{html.escape(label.strip())}</span></label>')

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
<div class="lookup">
  <input id="who" type="text" placeholder="e.g. Leo Radzihovsky">
  <button class="act" id="go">Look up</button>
</div>
<div id="out"></div>
</section>

<section><h2>Step 3 · Which arXiv sections?</h2>
<p class="explain">Pick the whole archive, or specific subsections. Choosing
<b>cond-mat</b> already includes everything indented under it.</p>
<div class="checks">{''.join(boxes)}</div>
<div class="actions"><button class="act" id="savefeeds">Save sections</button>
<span id="feedmsg" class="count"></span></div>
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
const out=document.getElementById('out'), who=document.getElementById('who');
function busy(m){{out.innerHTML='<p class="explain"><span class="spin"></span>'+m+'</p>';}}
async function search(){{
  const n=who.value.trim(); if(!n) return;
  busy('Searching...');
  const r=await jpost('/api/profile/search',{{who:n}});
  if(!r.ok){{out.innerHTML='<p class="explain">'+r.error+'</p>';return;}}
  if(!r.matches.length){{out.innerHTML='<p class="explain">Nobody found by that name.</p>';return;}}
  out.innerHTML='<h4>Which one?</h4>'+r.matches.map((m,i)=>
    '<div class="match"><b>'+m.name+'</b> — '+(m.institution||'affiliation unknown')+
    '<div class="meta">'+m.works+' papers · '+m.cited.toLocaleString()+' citations</div>'+
    '<div class="meta">'+(m.topics||[]).slice(0,4).join(' · ')+'</div>'+
    '<div class="actions"><button class="act" data-pick="'+i+'">Use this person</button></div></div>').join('');
}}
document.getElementById('go').addEventListener('click',search);
who.addEventListener('keydown',e=>{{if(e.key==='Enter')search();}});
out.addEventListener('click',async e=>{{
  const p=e.target.closest('[data-pick]'); if(!p) return;
  busy('Reading the corpus — up to a minute...');
  const r=await jpost('/api/profile/apply',{{who:who.value.trim(),pick:+p.dataset.pick}});
  out.innerHTML='<p class="explain">'+(r.ok
    ? 'Added their coauthors and topics to your config. You can prune the list later in config.toml.'
    : r.error)+'</p>';
}});
document.getElementById('savefeeds').addEventListener('click',async ()=>{{
  const f=[...document.querySelectorAll('.checks input:checked')].map(i=>i.value);
  const r=await jpost('/api/setup/feeds',{{feeds:f}});
  document.getElementById('feedmsg').textContent =
    r.ok ? 'saved: '+r.feeds.join(', ') : r.error;
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
    tally = (f'{stats.get("total", 0)} papers announced · '
             f'{stats.get("new", 0)} new · '
             f'{stats.get("replacements", 0)} replacements'
             if stats else "")
    options = "".join(
        f'<option value="{d}"{" selected" if d == date else ""}>{d}</option>'
        for d in dates)
    npicks = len(picks)

    header = f"""<header><div class="bar">
  <h1>arXiv cond-mat</h1>
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

    return page(f"cond-mat {date}", header + f"<main>{body}</main>" + footer)


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
            else:
                pick.unrecord_ids([pid])
            return self._json({"ok": True, "total": len(pick.picked_ids())})

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

        if u.path == "/api/setup/done":
            SETUP_DONE.write_text("setup completed\n", encoding="utf-8")
            return self._json({"ok": True})

        if u.path.startswith("/api/profile/"):
            who = str(data.get("who") or "").strip()
            if not who:
                return self._json({"ok": False, "error": "give a name"}, 400)
            picked = str(int(data.get("pick") or 0))
            what = u.path.rsplit("/", 1)[-1]

            if what == "search":
                ok, res = run_profile(["--list", "--json", who], timeout=60)
                return self._json({"ok": True, "matches": res} if ok
                                  else {"ok": False, "error": res})
            if what == "build":
                ok, res = run_profile(["--json", "--pick", picked, who])
                if not ok:
                    return self._json({"ok": False, "error": res})
                return self._json({"ok": True, "works_seen": res["works_seen"],
                                   "coauthors": res["coauthors"],
                                   "terms": res["terms"], "toml": res["toml"]})
            if what == "apply":
                ok, res = run_profile(["--apply", "--pick", picked, who])
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
