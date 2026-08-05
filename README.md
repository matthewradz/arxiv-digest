# arXiv nightly digest

Reads a whole arXiv announcement every weeknight — for cond-mat that's ~100 new
papers plus ~55 replacements — and writes you a short digest: five papers worth
downloading with a one-line claim and three bullets each, a handful of
runners-up, a judgement about what is likely to matter, and what the night's
titles say about where the field is drifting.

It keeps a running library of every paper it surfaced, and learns from the ones
you actually want.

It runs entirely on your own machine and uses a **subscription you already pay
for** — ChatGPT Plus/Pro or Claude Pro/Max. No API key, no server, no account to
create, nothing uploaded.

## Installing

```bash
git clone <this-repo> ~/arxiv-digest
cd ~/arxiv-digest
./install-app.sh          # puts "arXiv Digest" in ~/Applications
```

Then open **arXiv Digest**. On first run it walks you through three steps:

1. **Sign in** — checks whether Claude Code or Codex CLI is installed and
   actually working, and tells you what to run if not.
2. **Whose work to follow** — type a researcher's name and it reads their real
   publication record for coauthors and topics.
3. **Which arXiv sections** — the whole of cond-mat, specific subsections like
   `cond-mat.soft`, or other archives such as `hep-th` and `quant-ph`.

Then it builds your first digest. You can revisit all of it later via
**Settings** at the bottom of the page.

Requires macOS and Python 3.11+. To send it to someone who won't use git, run
`./package.sh` for a zip with a double-clickable installer.

## Using it

Open **arXiv Digest** from your Applications folder. It builds tonight's digest,
shows you the progress, and opens it in your browser. Each of the five papers has
an *abstract* and *PDF* button, and a **Want this** button — that last one is how
the tool learns what you like. Clicking it again takes it back.

The reader quits by itself once you stop using it. Nothing runs in the
background except the nightly build, if you turned that on.

If you'd rather stay in a terminal, the app is only a wrapper — the shell tools
do the same job and write the same files:

```bash
cd ~/arxiv-digest
./run.sh              # build tonight's digest
./run.sh --open       # build it and open the markdown
python3 app.py        # same as the app
```

Takes a couple of minutes. The digest lands in `digests/YYYY-MM-DD.md`, named
for the arXiv listing date. Running twice on the same listing does nothing —
it will not rebuild or overwrite unless you pass `--force`.

To have it run on its own every weeknight at 9pm, with a notification when the
digest is ready:

```bash
./install-schedule.sh          # or: ./install-schedule.sh 20 30  for 20:30
./install-schedule.sh --remove # to stop
```

arXiv announces Sunday through Thursday evening, producing the Monday-to-Friday
listings, so nothing is scheduled at the weekend.

## Which sections it reads

Set in the wizard, or by hand as `feeds` in `config.toml`:

```toml
feeds = ["cond-mat"]                        # a whole archive
feeds = ["cond-mat.soft", "cond-mat.str-el"] # just some subsections
feeds = ["cond-mat", "hep-th", "quant-ph"]   # several archives
```

Anything with an arXiv RSS feed works. Naming a whole archive already includes
its subsections, so don't list both.

## Teaching it your taste

This is the part that makes it get better.

1. Click **Want this** on the papers you'd actually download, as you read.
   Clicking it again undoes it.
2. Every week or two, click **Retune from my picks** at the bottom of the page.

That's it. If you prefer files: every paper is also a checkbox in `library.md`
(change `- [ ]` to `- [x]`), and `./learn.sh` does the same retune from the
command line. The app, the file and the command line all write to the same
place, so you can mix them freely.

Retuning compares what it showed you against what you took, and rewrites
`preferences.md` — which is fed into the digest every night from then on. It also
writes `config-suggestions.md`: specific proposed edits to your author list and
topic weights, with the evidence for each. **Nothing is applied automatically.**
Read them and edit `config.toml` yourself if you agree.

Expect the suggestions to be thin until about 10–15 picks have accumulated; with
fewer than that it will tell you the sample is too small rather than invent
patterns.

You can also record picks directly, without the checkboxes:

```bash
python3 pick.py 2607.29521 2607.28734    # papers you downloaded
python3 pick.py --list                   # what has been recorded
```

## Which subscription it uses

The digest needs a model to read the abstracts. It works with either coding CLI,
whichever you have installed — it picks one automatically:

| you pay for | install | sign in with |
|---|---|---|
| ChatGPT Plus / Pro | `npm install -g @openai/codex` | run `codex`, choose **Sign in with ChatGPT** |
| Claude Pro / Max | `curl -fsSL https://claude.ai/install.sh \| bash` | run `claude`, sign in with your Claude account |

**Sign in with the subscription, not an API key.** An API key is billed
separately per use; the subscription you already pay for covers this. Two
gotchas worth knowing:

- If `ANTHROPIC_API_KEY` is set in your environment, Claude Code uses it *instead*
  of your Pro/Max plan and you get charged per use. `claude` then `/status` shows
  which one is active; `claude logout` and `claude login` resets it.
- Codex CLI's ChatGPT sign-in has been reported to create an API key against your
  API org in some setups. If you see unexpected API charges, check
  platform.openai.com for a key you didn't mean to use.

To force one when both are installed, or to pick a model:

```bash
DIGEST_ENGINE=codex ./run.sh
DIGEST_MODEL=gpt-5.1-codex ./run.sh
```

## Building the lists from someone's actual papers

Rather than guessing an author list, point it at a researcher and let it read
their real publication record:

```bash
python3 profile.py "Your Name"           # show what it would add
python3 profile.py "Your Name" --apply   # merge it into config.toml
```

Or click **Tune from a researcher** at the bottom of the reader.

It pulls their frequent coauthors (with joint-paper counts) and the recurring
phrases from their own paper titles, and offers both as config entries. `--apply`
merges without removing anything and keeps `config.toml.backup`.

The counts are the point: **frequency is not interest.** A liquid-crystal
theorist's most frequent coauthors include the experimental chemists on those
papers, and you probably don't want to track all of them. Prune the list.

Data comes from [OpenAlex](https://openalex.org), which is free, needs no key,
and indexes essentially all of the physics literature. Google Scholar has no API
and blocks automated access, so a Scholar profile can't be read directly — give
the name instead and you get the same corpus from a source that permits it.

## Tuning it by hand

Everything lives in **`config.toml`**, which is the only file you need to touch.

- **`[authors] names`** — people whose papers you want to see whatever they are
  about. A fresh install starts with ~184 well-known names across the topic areas
  below; setup step 2 adds a specific researcher's real coauthors on top.
  **Prune it** — a shorter, sharper list gives better digests than a long one, and
  coauthor lists include experimentalists you may not want. Matching is on last
  name plus first initial, so occasionally the wrong J. Kim slips through; the
  digest always shows which name matched.
- **`[[topics]]`** — keyword groups with weights. Each keyword is a
  case-insensitive regular expression tested against title and abstract; title
  hits count double. Set a weight to `0` to mute a topic without deleting it.
- **`[settings]`** — how many papers get full abstracts (`max_candidates`), the
  relevance floor (`min_score`), and how strictly replacements are filtered.

The scoring is a deliberately generous prefilter, not a judgement. Its only job
is to cut ~155 papers down to ~34 without dropping anything good. The actual
reading and ranking is done from full abstracts afterwards, which is why keyword
false positives (a "black hole membrane paradigm" paper, say) get discarded
rather than recommended.

To change the digest's voice, priorities or format, edit **`prompt.md`** — it is
plain English and is where the "who you are writing for" instructions live.

## How it works

The whole thing is one pipeline. Nothing is a framework, nothing is generated,
and no step knows about any step but the next one.

```
arXiv RSS  ──►  fetch.py  ──►  runs/DATE/brief.md  ──►  model CLI  ──►  digests/DATE.md
                   ▲                                    (claude|codex)        │
                   │                                                          ▼
             config.toml ◄── profile.py (OpenAlex)                       record.py
             preferences.md ◄── learn.py ◄── picks.jsonl ◄── pick.py ◄── library.md
```

1. **`fetch.py`** downloads the RSS feed for each section in `feeds`, parses every
   paper, and scores it: points per matched author, points per topic keyword
   (doubled for title hits), bonuses for reviews and cross-lists. It writes the
   top ~34 with full abstracts into `runs/DATE/brief.md`, along with the reader
   description, the learned preferences, and the bare titles of everything else.
   **All the arithmetic lives here. No model is involved.**
2. **`run.sh`** pipes that briefing into whichever model CLI is installed and
   captures the result as `digests/DATE.md`. **All the judgement lives here** —
   which five papers, what to say, what the trends are — and it is steered
   entirely by `prompt.md`, which is plain English you can edit.
3. **`record.py`** appends every surfaced paper to `library.md` as a checklist,
   and warns if the digest referenced a paper that wasn't in the announcement.
4. **`pick.py`** records what you marked as wanted into `picks.jsonl`, keeping
   `library.md`, the app and the command line in sync.
5. **`learn.py`** compares picks against everything shown and emits a statistics
   report; `learn.sh` has the model turn it into `preferences.md`, which step 1
   then injects into every future briefing. That's the feedback loop.
6. **`app.py`** is a local web server that does none of the above itself — it
   shells out to `run.sh`, `learn.sh` and `profile.py` and renders their output.
   Delete it and the shell tools still work identically.

Two deliberate properties: **the scoring never decides anything** (it only
narrows 150 papers to 34, generously, so keyword false positives get discarded by
the model rather than recommended), and **every artefact is plain text you can
read** — including `brief.md`, the exact input the digest was written from.

## What is where

| file | lines | what it is |
|---|---|---|
| `config.default.toml` | ~250 | the defaults a fresh install starts from (in the repo) |
| `config.toml` | — | **your copy — the only file you need to edit.** Never committed |
| `prompt.md` | ~120 | what the digest should say and how briefly. Plain English |
| `learn_prompt.md` | ~60 | how to turn your picks into `preferences.md` |
| `fetch.py` | ~500 | feed parsing, name matching, scoring, briefing. No model |
| `run.sh` | ~130 | the nightly pipeline: fetch → model → digest → library |
| `app.py` | ~1100 | local reader: setup wizard, digest page, pick buttons |
| `pick.py` | ~200 | records what you wanted; the one source of truth for picks |
| `record.py` | ~120 | writes `library.md` from a digest |
| `learn.py` | ~200 | the shown-vs-picked statistics report |
| `learn.sh` | ~80 | runs the report through the model into `preferences.md` |
| `profile.py` | ~380 | builds author/topic lists from a real corpus (OpenAlex) |
| `_engine.sh` | ~90 | picks Claude Code or Codex CLI, whichever is installed |
| `_python.sh` | ~35 | finds a Python 3.11+ (macOS ships 3.9) |
| `install-app.sh` | ~90 | builds the clickable `arXiv Digest.app` |
| `install-schedule.sh` | ~110 | the weeknight launchd job |
| `package.sh` | ~110 | builds the shareable zip |
| `Install.command` | ~160 | double-clickable installer inside the zip |
| `test-fresh.sh` | ~100 | stash your data, try the first-run flow, restore |

Produced at runtime, none of it committed: `digests/DATE.md`, `library.md`,
`picks.jsonl`, `preferences.md`, `config-suggestions.md`,
`profile-suggestions.toml`, `runs/DATE/` (raw feed, scores, briefing) and
`logs/`.

## Where your data lives

Everything is plain text inside `~/arxiv-digest`. Nothing is stored anywhere
else, nothing is uploaded, and you can read or delete any of it by hand.

| what | where | notes |
|---|---|---|
| the digests | `digests/2026-08-03.md` | one markdown file per listing date |
| every paper surfaced | `library.md` | checkbox list, newest date at the top |
| your downloads | `picks.jsonl` | one JSON line per pick — **the training data** |
| learned taste | `preferences.md` | written by retuning, fed into every digest |
| proposed config edits | `config-suggestions.md` | written by retuning, never auto-applied |
| profile suggestions | `profile-suggestions.toml` | written by `profile.py`, never auto-applied |
| config backup | `config.toml.backup` | kept whenever `profile.py --apply` runs |
| the raw feed and scores | `runs/2026-08-03/` | `items.json` (all 158 papers), `candidates.json` (the shortlist with scores), `brief.md` (exactly what was sent to Claude) |
| what happened on each run | `logs/2026-08-03.log` | plus `app.log` for the reader |

`runs/DATE/brief.md` is the useful one for checking the tool's work: it is the
complete input the digest was written from, so you can see whether something was
missed because it was never shortlisted, or shortlisted and then passed over.

To back it up or move it to another machine, copy the whole folder. To start the
learning over, delete `picks.jsonl` and `preferences.md`.

## Sharing it with someone else

```bash
./package.sh          # writes ~/Desktop/arxiv-digest.zip
```

They unzip it, double-click `Install.command`, and answer two prompts. The zip
deliberately leaves out your picks, preferences and logs, so they start with
their own empty history — but it does include one sample digest so the format is
visible before the first real run.

Their machine needs Python 3.11+ and either Claude Code or Codex CLI; the
installer checks and prints what to do if something is missing.

## If something goes wrong

- **"no model CLI found"** — install Claude Code or Codex CLI (see above) and
  sign in. If one is installed somewhere unusual, set `CLAUDE_BIN=/path/to/claude`
  or `CODEX_BIN=/path/to/codex`.
- **A digest looks wrong or truncated** — the partial output is kept at
  `runs/DATE/digest.partial.md` and the previous digest is left untouched. Check
  `logs/DATE.log`, then rerun with `./run.sh --force`.
- **Nothing new tonight** — if it says the listing is already digested, arXiv has
  not announced since the last run. Weekends and US holidays are quiet.
- **Too many / too few papers** — raise or lower `min_score` in `config.toml`.
  Roughly: 2.0 shortlists ~30 papers a night, 5.0 shortlists ~15.

Requires Python 3.11+ (uses `tomllib`) and either Claude Code or Codex CLI.
No pip installs, no API key.
