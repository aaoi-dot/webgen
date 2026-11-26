#!/usr/bin/env python3
"""site.py: turn a domain name, a short theme and a persona manifest into a plain HTML/CSS/JS website.

    python site.py --domain statpoint.co.uk --theme "API for web statistics and marketing, small family-run business"
    python site.py --domain ... --theme ... --manifest manifests/2016-competent-freelancer-4f3f.json
    python site.py --domain ... --theme ... --yes          # no questions, no plan check
    python site.py --domain statpoint.co.uk                # resume an unfinished site
    python site.py --list sites.txt                        # many sites: one "domain | theme | notes" per line
    python site.py --domain ... --theme ... --dry-run      # show prompts and cost estimate, no calls

Steps per site, each saved in out/<domain>/ so a run can stop and resume:
  1. assess:  theme -> earliest plausible year, business type, moods       (small call)
  2. pick:    an unused manifest that fits; claimed so nobody else uses it  (local)
  3. plan:    theme + persona -> business facts, pages and sections -> plan.json
  4. content: plan + voice -> all the copy -> content.md
  5. build:   era + visual + authorship + content.md -> *.html, *.css, *.js, *.svg
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

# =============================================================================
# CONFIG
# =============================================================================

ASSESS_MODEL = "claude-opus-5-5"
PLAN_MODEL = "claude-opus-5-5"
CONTENT_MODEL = "claude-opus-5-5"
BUILD_MODEL = "claude-opus-5-5"
ASSESS_EFFORT = "low"
PLAN_EFFORT = "medium"
CONTENT_EFFORT = "medium"
BUILD_EFFORT = "high"
ASSESS_MAX_TOKENS = 8_000
PLAN_MAX_TOKENS = 32_000
CONTENT_MAX_TOKENS = 32_000
BUILD_MAX_TOKENS = 64_000
OUT_DIR = Path("out")
MANIFEST_DIR = Path("manifests")
USED_DIR = ".used"         # inside the manifests folder: one claim file per used manifest
JOBS = 4                   # sites running at once (direct API mode)
ATTEMPTS = 3               # tries per step when output is unusable
BATCH_THRESHOLD = 20       # this many pending sites or more -> Batch API
BATCH_POLL_SECONDS = 60
USE_FALLBACKS = True       # server-side refusal fallback (direct calls only; not allowed in batches)
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1"}

# USD per million tokens: input, output, cache write (5m), cache read
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
    "claude-fable-5-2": (8.00, 40.00, 10.00, 0.20),
}
BATCH_DISCOUNT = 0.5

ALLOWED_EXT = {".html", ".css", ".js", ".svg", ".txt", ".webmanifest", ".xml"}
DONE_FILE = "build.json"   # written last; its presence means the site is complete
ERROR_PAGE_ID = "404"      # every site gets 404.html, written in its voice and built in its style
STEPS = ("assess", "plan", "content", "build")

# =============================================================================
# LIBRARIES
# =============================================================================

# Kept in step with manifest.py.
BUSINESS_TYPES = [
    "solo founder", "freelancer", "boutique studio", "agency", "consultancy", "family business",
    "small local firm", "seed-stage startup", "bootstrapped SaaS", "venture-backed scale-up",
    "enterprise product team", "open-source project", "research spin-out",
]
MOODS = ["warm", "playful", "sombre", "professional", "technical", "luxury", "energetic", "community",
         "clinical", "creative", "calm", "earthy"]
ELEMENTS = ["long-text", "list", "table", "timeline", "gallery", "form", "code", "faq", "quote", "downloads", "notice"]

# Rolled per site from the domain name, so that similar themes still get different plans.
SECTION_IDEAS = [
    "a changelog or 'what's new' list", "a comparison table", "a short glossary of terms",
    "a 'how we work' timeline", "a downloads list (guides, sample files, spec sheets)",
    "an honest 'who this isn't for' section", "a worked example, start to finish",
    "a short history timeline", "a list of supported integrations, formats or sources",
    "a quickstart or code sample", "the team, described by role", "a description of a sample report or output",
    "case notes from a few customers (described, never named)", "a limits and fair-use table",
    "a 'before you get in touch' checklist", "a newsletter or updates signup", "a status or reliability note",
    "a 'common mistakes' list", "a plain-language explanation of how it works under the hood",
    "a small FAQ", "a 'what you get' checklist for each plan or service",
]
AVOID_PATTERNS = [
    "a hero band followed by three feature cards", "a testimonial carousel", "a row of big statistic numbers",
    "a closing call-to-action band on every page", "icon + heading + one-line grids",
    "an 'our values' section of single words", "a 'trusted by' strip", "a pricing page with a highlighted 'most popular' plan",
    "alternating image/text rows down the page", "a 'meet the team' grid of cards",
]

AI_CLICHES = [
    "elevate", "seamless", "seamlessly", "unlock", "delve", "crafted with passion", "passionate about",
    "nestled", "tapestry", "journey", "embark", "curated", "bespoke experience", "unparalleled",
    "cutting-edge", "state-of-the-art", "game-changer", "look no further", "in today's fast-paced world",
    "whether you're", "we pride ourselves", "second to none", "one-stop shop", "dive in", "discover the difference",
    "testament to", "vibrant", "a haven for", "where X meets Y", "more than just a", "experience the magic",
    "your trusted partner", "taking it to the next level", "holistic", "empower", "revolutionise", "synergy",
    "supercharge", "harness the power", "unleash", "next-gen", "best-in-class", "robust", "leverage", "streamline",
    "transform your", "at scale without compromise", "AI-powered everything",
]

# =============================================================================
# PROMPTS (stable text first so prompt caching applies)
# =============================================================================

ASSESS_SYSTEM = f"""You read the theme for a small business or project website and answer three questions about it as JSON.

- earliest_year: the first year, between 2012 and 2026, in which a business like this could plausibly have had \
this website, judged by the technology and market it depends on (for example, AI video generation: 2023; a \
mobile ad network: 2012). If it could have existed by 2012, answer 2012.
- business_type: the closest of: {", ".join(BUSINESS_TYPES)}.
- moods: one to three moods the site's design should suit, from: {", ".join(MOODS)}.
"""

PLAN_SYSTEM = f"""You plan the content of a small real website before anyone writes the copy. You are given the \
domain name, a short theme, optional notes from the client's staff, and a persona: who wrote the copy, their voice, \
the year the site was built, later edits, and the structural habits of the person who built it. You return the \
business facts and the page plan as JSON.

The business:
- wordmark: the business name as it appears in the logo, taken from the domain the way a real business styles its \
own name (statpoint.co.uk -> "Statpoint", get-tidy.io -> "Tidy", bluefinch-data.com -> "Bluefinch Data").
- what_they_do: one plain sentence. audience: who it is for, in a few words.
- business_type: use the one given.
- founded: a year no later than the build year. team_size: a believable number for the business type.
- offerings: three to six things they actually sell or provide, each with a short concrete detail. Specific beats \
generic: name the real thing, its limits, formats, turnaround or method.
- quirks: one or two small true-sounding details that make this business itself and not another one.
- Staff notes win over your own ideas. Use them as given.
- No people's names, addresses, places, regions, countries, phone numbers, email addresses, opening hours or \
price amounts. Never name real companies, products or tools.

The pages:
- Follow the persona's page count; you may move one step up or down if the business clearly needs it. The first \
page has id "index" and title "Home" or similar. Other page ids are short, lowercase and hyphenated.
- Section ids are short, lowercase, hyphenated and describe the content ("data-sources", "how-billing-works"), \
never generic ("section-2", "feature-grid").
- element is one of: {", ".join(ELEMENTS)}. length is short, medium or long. Use the persona's favourite elements \
more than the others, and follow the persona's structural habit.
- Include every "must include" item, or its nearest sensible equivalent if one is clearly wrong for the business. \
Do not use the "avoid" patterns.
- Do not fall back on the stock plan of intro, three features, testimonials and a call to action. Plan what this \
business would actually need to say, in the order its visitors would want it.
- graphic: true only where a simple SVG or CSS drawing would help, and only if the persona's graphics allow it.
- note: a short content note for the copywriter, or an empty string.
- added_in: 0 for pages in the original build. If a later edit added a page, plan that page with added_in set to \
the edit's year.
- A pricing page, if any, shows tiers, limits and features, never amounts.
- Don't plan an error page: a 404 page is added to every site automatically.
"""

CONTENT_SYSTEM = f"""You write the words for real-world websites. You are given a brief about one business or \
project and its site plan, and you write all of the copy in the voice of whoever the brief says wrote it.

How to write:
- Write as the named copy author would. A founder writes the way they talk, with opinions and the odd \
clumsy phrase. A product marketer is benefit-led and tidy, and leans on headings. A professional copywriter is \
polished but still specific. The developer who built it is literal, precise and a bit terse, and reaches for \
code. "The web author's filler, edited by the founder" reads as generic placeholder copy that has been partly \
rewritten, so some sections sound personal and others sound templated. A docs writer is clear, structured and \
second-person.
- Use the given tone and personality throughout. Match the sample sentence's rhythm without copying it more \
than once. Staff notes about tone take priority.
- Use the facts given: wordmark, founding year, offerings and their details, and quirks. Invent ordinary supporting \
detail where needed (dates, features, limits, FAQs, release notes, endpoints), consistent with the brief and the \
build year. Never contradict a given fact.
- The business is called by its wordmark, and its domain may appear where a real site would show it (the footer, \
a copyright line, hosts in code samples such as api.<domain>). Never write a person's name, place name, address, \
region or country, phone number, email address, social media handle, opening hours or price. People are \
described by role, never named. Customers in case studies and quotes are described, not named ("a mid-size \
retailer", "a mobile games publisher"). Don't name real companies, products or tools; describe them generically \
("your data warehouse", "the major ad platforms"). Nothing should tie the site to a country: no currency, no \
regional terms, no national bodies or schemes.
- Write for the site's build year. Mention nothing that happened after it (later edits, listed separately, can \
add their own dated content such as a notice).
- Specific beats generic. Concrete nouns, real numbers, small true-sounding details. Uneven lengths are fine; \
real sites are uneven.
- Never use these AI-sounding words and phrases: {", ".join(AI_CLICHES)}. Avoid tidy triads, rhetorical \
questions as openers and an upbeat closing line on every section.
- Do not write testimonials that sound like marketing. If the plan has a quote or reviews, make them short, \
plain and attributed by description only ("a regular", "a first-time customer"), never by name.

Output format: Markdown only, nothing before or after it, exactly this structure:

# <Wordmark>: site content

## page: <page id> (<page title>)

### section: <section id> [<element>]
<the copy for that section>

Rules for the format:
- Include every page and every section from the plan, in plan order, with the ids and element types exactly as given.
- The words are content only: no layout, colours, fonts or styling instructions.
- Tables are Markdown tables. Lists are Markdown lists. FAQ sections are question lines in bold, each followed by \
its answer. Forms list their fields and the button label: "Fields: ... Button label: ...". Code sections \
are a one-line lead-in then a short fenced code block; use hosts under the site's domain or example.com, \
fake keys like sk_test_xxx, and code that would plausibly work for the build year. Pricing tables give tier \
names, who each is for, limits and features, and a call to action ("Start free", "Contact sales"), never an \
amount or currency. Galleries and portfolios list each item with a title and a one-line caption. Downloads list \
file names with a short description (the files do not need to exist). Timelines are dated lines.
- If a section is marked graphic: true, end it with a line starting "Graphic note:" describing a simple drawing \
that can be made in SVG or CSS (subject, style, roughly where it sits). No photos.
- Optional "Note:" lines carry small content notes for the builder.
- A page with added_in is a later addition: write it for that year.
- The page with id "404" is the site's error page. Keep it short and in the same voice and tone as the rest of \
the site: say plainly that the page isn't here, maybe why (a moved page, a mistyped link), and point to the main \
pages by name. A small joke only if this voice would make one. No apology essay.
- If the brief lists a later edit that adds visible content (a notice, a cookie banner, a hiring notice), \
add a section at the end of the relevant page: "### section: edit-<year> [notice]" with that text. If a later \
edit plainly doesn't fit this business, reinterpret it as the nearest change that does.
"""

BUILD_SYSTEM = """You are a web author building a small real website by hand. You are given a brief that \
describes who you are, when you are building the site, how it should look, and the finished copy. Build it \
the way that person would have built it in that year.

Be the author:
- Write the code as the brief's author would, with their design skill, code skill, background and habits: \
naming style, units, reset, media query approach, file layout, markup style, JavaScript approach, icons, \
commenting habit and level of imperfection. A backend specialist's site should look like a backend \
specialist built it; a graphic designer's site should look designed but have messy CSS; a founder-engineer's \
site should be quick and functional with a few rough edges; an enterprise web team's site should be cautious, \
wrapper-heavy and on-brand. Do not "improve" on the author.
- Class and id names come from the content and the author's naming habit. Section ids in the copy are only \
there to tell you where content goes; do not copy them into the code unless the author would name things \
that way. Do not use the generic names .container, .hero, .card, .btn-primary unless this author \
plainly would.
- Imperfections are human and small: a selector that is no longer used, a magic number, an inconsistent \
margin, a comment that is out of date, a style repeated in two places. The site must still work: valid \
enough to render correctly, links between pages working, forms with labels, nothing broken on load.

Respect the era:
- Use only HTML, CSS and JavaScript features that existed and were in normal use in the build year. The brief \
lists what is available and what is not yet; anything in the "not yet" list is forbidden in the original \
build. If the author would have used jQuery, load it from a public CDN URL (code.jquery.com) with a version \
from that year.
- Design trends also follow the year and the stated design direction.
- Later edits are applied on top of the original build in their own year. They may use features from their \
own year and may look visibly different from the original (a bolted-on cookie banner, a new button colour, \
a hamburger menu that does not quite match). Mark them the way the author would (a dated comment, a separate \
file or a block at the end of the stylesheet).

Visual rules:
- Use the palette hex values, fonts, type scale, base size, layout, shape, motion and graphics as given. Web \
fonts load from Google Fonts with a <link> unless the brief says self-hosted (then write an @font-face pointing \
at a fonts/ folder and fall back gracefully, since the font files are not supplied).
- No photographs and no image files to fetch. No hotlinked images. Every graphic is inline SVG, an .svg file \
you write, or CSS. Follow each "Graphic note" in the copy. An icon font, if the brief says so, loads from a \
public CDN (Font Awesome version appropriate to the year).
- Do not fall back on the stock layout of a hero, three feature cards, testimonials and a call-to-action band \
unless the section plan actually asks for that.
- No frameworks or build tools: no Bootstrap, Tailwind, React or similar. Plain HTML, CSS and JS only.

Content rules:
- Use the copy as written. You may split it into sensible markup, but do not rewrite it, add marketing lines \
or drop sections. Each page in the copy becomes one HTML file: the page with id "index" is index.html and \
every other page is <page id>.html.
- The page with id "404" becomes 404.html, the site's error page. It uses the same header, navigation, footer, \
styling and author habits as every other page, so it plainly belongs to the site. Because a server shows it at \
any missing address, every link and asset path in 404.html is root-relative (/style.css, /index.html, \
/sdks.html). Add <meta name="robots" content="noindex"> to its head.
- Code blocks are real <pre><code> with the author's styling; a copy button only if the author and year fit.
- Forms need an action; use action="#" or a plausible form handler path, with no working backend.
- The logo or wordmark is the business's wordmark from the brief, drawn the way this author would. Never add an \
address, phone number, email, map or opening hours, and leave social links as icons with href="#".

Output format: output only files, nothing else, each one like this:

=== FILE: relative/path.ext ===
<file contents>
=== END FILE ===

Paths are relative to the site root, use forward slashes, and contain no "..". Allowed extensions: .html, .css, \
.js, .svg, .txt, .xml, .webmanifest. Every page from the copy must be present, including 404.html. Write complete files; never \
abbreviate with "..." or "rest of the code here".
"""

# --- JSON schemas for structured output ----------------------------------------


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


ASSESS_SCHEMA = _obj({
    "earliest_year": {"type": "integer"},
    "business_type": {"type": "string", "enum": BUSINESS_TYPES},
    "moods": {"type": "array", "items": {"type": "string", "enum": MOODS}},
})

PLAN_SCHEMA = _obj({
    "wordmark": {"type": "string"},
    "business": _obj({
        "what_they_do": {"type": "string"},
        "audience": {"type": "string"},
        "business_type": {"type": "string"},
        "founded": {"type": "integer"},
        "team_size": {"type": "integer"},
        "offerings": {"type": "array", "items": _obj({"name": {"type": "string"}, "detail": {"type": "string"}})},
        "quirks": {"type": "array", "items": {"type": "string"}},
    }),
    "pages": {"type": "array", "items": _obj({
        "id": {"type": "string"},
        "title": {"type": "string"},
        "added_in": {"type": "integer"},
        "sections": {"type": "array", "items": _obj({
            "id": {"type": "string"},
            "element": {"type": "string", "enum": ELEMENTS},
            "length": {"type": "string", "enum": ["short", "medium", "long"]},
            "graphic": {"type": "boolean"},
            "note": {"type": "string"},
        })},
    })},
})

# =============================================================================
# SITE STATE (everything lives in out/<domain>/)
# =============================================================================


class BadOutput(Exception):
    pass


class Stop(Exception):
    """The user chose to stop; state is saved and the site can be resumed."""


def clean_domain(d: str) -> str:
    d = re.sub(r"^[a-z]+://", "", d.strip().lower()).split("/")[0].removeprefix("www.")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+", d):
        raise SystemExit(f"not a domain name: {d!r}")
    return d


class Site:
    def __init__(self, domain: str):
        self.domain = clean_domain(domain)
        self.dir = OUT_DIR / self.domain
        self.state_path = self.dir / "site.json"
        self.state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {}
        self.lock = threading.Lock()

    # files
    @property
    def plan_path(self): return self.dir / "plan.json"
    @property
    def content_path(self): return self.dir / "content.md"
    @property
    def manifest_path(self): return self.dir / "manifest.json"

    def exists(self): return self.state_path.exists()
    def complete(self): return (self.dir / DONE_FILE).exists()

    def save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_path)

    def manifest(self) -> dict:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def plan(self) -> dict:
        return json.loads(self.plan_path.read_text(encoding="utf-8"))

    def content(self) -> str:
        return self.content_path.read_text(encoding="utf-8")

    def next_step(self) -> str | None:
        if "assess" not in self.state:
            return "assess"
        if not self.manifest_path.exists():
            return "pick"
        if not self.plan_path.exists():
            return "plan"
        if not self.content_path.exists():
            return "content"
        if not self.complete():
            return "build"
        return None

    def add_usage(self, step: str, usages: list, batch=False):
        self.state.setdefault("usage", []).extend(dict(u, step=step, batch=batch) for u in usages)
        self.save()

    def total_cost(self) -> float:
        return sum(cost(u, u.get("batch", False)) for u in self.state.get("usage", []))


def new_site(domain, theme, notes, manifest=None) -> Site:
    s = Site(domain)
    if s.exists():
        if theme and theme != s.state["theme"]:
            say(f"  {s.domain}: already started with theme \"{s.state['theme']}\"; resuming that. "
                f"Delete {s.dir}/ to start over.")
        return s
    if not theme:
        raise SystemExit(f"{s.domain}: no theme given and nothing to resume in {s.dir}/")
    s.state = {"domain": s.domain, "theme": theme, "notes": notes or {}, **roll(s.domain),
               "requested_manifest": str(manifest) if manifest else None, "started": now()}
    s.save()
    return s


def roll(domain: str) -> dict:
    """Per-site constraints for the plan, seeded by the domain so a rerun gets the same ones."""
    rng = random.Random(int(hashlib.sha256(domain.encode()).hexdigest(), 16))
    return {"must_include": rng.sample(SECTION_IDEAS, k=rng.choice([1, 2, 2])), "avoid": rng.sample(AVOID_PATTERNS, k=2)}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

# =============================================================================
# MANIFEST PICKING
# =============================================================================

_pick_lock = threading.Lock()


def used_dir() -> Path:
    return MANIFEST_DIR / USED_DIR


def claimed_by(name: str) -> str | None:
    p = used_dir() / name
    return p.read_text(encoding="utf-8").strip() if p.exists() else None


def claim(name: str, domain: str) -> bool:
    used_dir().mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(used_dir() / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return claimed_by(name) == domain
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(domain + "\n")
    return True


def is_persona(m: dict) -> bool:
    return "voice" in m and "structure" in m


def fit_problems(m: dict, assess: dict) -> list[str]:
    out = []
    if m["era"]["build_year"] < assess["earliest_year"]:
        out.append(f"built in {m['era']['build_year']}, but this theme is plausible only from {assess['earliest_year']}")
    if assess["business_type"] not in m["authorship"].get("suits", BUSINESS_TYPES):
        out.append(f"a {m['authorship']['author']} rarely builds for a {assess['business_type']}")
    return out


def persona_line(name: str, m: dict) -> str:
    v, a = m["voice"], m["authorship"]
    return (f"{name}: {m['era']['build_year']} · {a['author']} · {m['visual']['direction']} · "
            f"{v['tone']} ({v['personality']}), copy by {v['copy_author']}")


def check_requested(path: Path, domain: str) -> None:
    """Fail early, before any paid call, if --manifest can't be used."""
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"can't read {path}: {e}")
    if not is_persona(m):
        raise SystemExit(f"{path} is an old-format (niche) manifest; make new ones with manifest.py")
    owner = claimed_by(path.name)
    if owner and owner != clean_domain(domain):
        raise SystemExit(f"{path.name} was already used for {owner}")


def pick_manifest(site: Site, ask_user: bool, force: bool) -> None:
    """Choose a manifest for the site, claim it and copy it into the site folder."""
    assess = site.state["assess"]
    req = site.state.get("requested_manifest")
    with _pick_lock:
        if req:
            path = Path(req)
            m = json.loads(path.read_text(encoding="utf-8"))
            if not is_persona(m):
                raise Stop(f"{path} is an old-format (niche) manifest; make new ones with manifest.py")
            owner = claimed_by(path.name)
            if owner and owner != site.domain:
                raise Stop(f"{path.name} was already used for {owner}")
            problems = fit_problems(m, assess)
            if problems and not force:
                say(f"  {site.domain}: {path.name} doesn't fit well: " + "; ".join(problems))
                if not ask_user or input("  use it anyway? [y/N] ").strip().lower() != "y":
                    raise Stop("choose another manifest, leave out --manifest to pick one, or pass --force")
            if not claim(path.name, site.domain):
                raise Stop(f"{path.name} was just claimed by someone else")
        else:
            cands, old = [], 0
            for p in sorted(MANIFEST_DIR.glob("*.json")):
                if p.name.startswith(".") or (used_dir() / p.name).exists():
                    continue
                try:
                    m = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not is_persona(m):
                    old += 1
                    continue
                if not fit_problems(m, assess):
                    cands.append((len(set(m["taste"]) & set(assess["moods"])), p, m))
            if old:
                say(f"  note: skipped {old} old-format manifest(s) in {MANIFEST_DIR}/")
            best = [c for c in cands if c[0] > 0] or cands
            random.shuffle(best)
            for _, path, m in best:
                if claim(path.name, site.domain):
                    break
            else:
                raise Stop(
                    f"no unused manifest fits (needs build year {assess['earliest_year']} or later and an "
                    f"author who builds for a {assess['business_type']}). Try: python manifest.py --count 50 "
                    f"--years {assess['earliest_year']}-2026")
    shutil.copyfile(path, site.manifest_path)
    site.state["manifest"] = path.name
    site.save()
    say(f"  {site.domain}: manifest {persona_line(path.name, m)}")

# =============================================================================
# BRIEF RENDERING
# =============================================================================


def _list(items):
    items = [str(i) for i in items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else "none"


def _a(word: str) -> str:
    return f"{'an' if word[:1].lower() in 'aeiou' else 'a'} {word}"


def _clause(s: str) -> str:
    """'Sells web statistics.' -> 'sells web statistics', to follow 'It' or 'which'."""
    s = s.strip().rstrip(".")
    return s[:1].lower() + s[1:] if s[:2] != s[:2].upper() else s


def _edits(m: dict) -> str:
    return "\n".join(f"- {e['year']}: {e['change']}" for e in m["era"].get("later_edits", [])) or "- none"


def _notes(site: Site) -> str:
    n = site.state.get("notes") or {}
    labels = {"offer": "What they do or offer", "tone": "Tone and voice", "mention": "Mention or avoid"}
    lines = [f"- {labels.get(k, k)}: {v}" for k, v in n.items() if v]
    return "\n".join(lines) or "- none"


def assess_brief(site: Site) -> str:
    return f"Domain: {site.domain}\nTheme: {site.state['theme']}\nStaff notes:\n{_notes(site)}\n"


def plan_brief(site: Site) -> str:
    m, a = site.manifest(), site.state["assess"]
    v, st, vis = m["voice"], m["structure"], m["visual"]
    return f"""Domain: {site.domain}
Theme: {site.state['theme']}
Business type: {a['business_type']}

Staff notes (these win):
{_notes(site)}

Persona:
- Copy written by {v['copy_author']}; tone {v['tone']}; {v['personality']}. Something they'd say: "{v['sample']}"
- The site was built in {m['era']['build_year']} by {_a(m['authorship']['author'])} ({m['authorship']['background']}).
- Later edits:
{_edits(m)}
- Pages: {st['pages']}. Content density: {st['density']}.
- Favourite elements: {_list(st['favourite_elements'])}.
- Structural habit: {st['habit']}.
- Graphics available: {_list(vis['graphics'])}.

Must include: {_list(site.state['must_include'])}.
Avoid: {_list(site.state['avoid'])}.
"""


def site_pages(site: Site) -> list[dict]:
    """The plan's pages plus the error page every site must have."""
    pages = site.plan()["pages"]
    if any(p["id"] == ERROR_PAGE_ID for p in pages):
        return pages
    graphic = site.manifest()["visual"]["graphics"] != ["none"]
    return pages + [{"id": ERROR_PAGE_ID, "title": "Page not found", "sections": [
        {"id": "not-found", "element": "long-text", "length": "short", "graphic": graphic,
         "note": "the error page: say plainly the page isn't here, in the site's own voice, then point to the main pages"}]}]


def _plan_lines(pages: list[dict]) -> str:
    out = []
    for p in pages:
        added = f" (added in {p['added_in']})" if p.get("added_in") else ""
        out.append(f"page {p['id']} \"{p['title']}\"{added}")
        for s in p["sections"]:
            note = f"; note: {s['note']}" if s.get("note") else ""
            out.append(f"  - section {s['id']} [{s['element']}], {s['length']}, graphic: {str(s['graphic']).lower()}{note}")
    return "\n".join(out)


def content_brief(site: Site) -> str:
    m, plan = site.manifest(), site.plan()
    b, v = plan["business"], m["voice"]
    offers = "\n".join(f"- {o['name']}" + (f": {o['detail']}" if o.get("detail") else "") for o in b["offerings"])
    n = b["team_size"]
    return f"""Here's the brief for {plan['wordmark']} ({site.domain}).

It {_clause(b['what_they_do'])}, for {b['audience']}. It's {_a(b['business_type'])}, going since {b['founded']}, \
with {n} {'person' if n == 1 else 'people'} involved.

What they offer:
{offers}

Worth knowing: {'; '.join(b['quirks'])}.

The voice is {v['personality']}. Something they'd say: "{v['sample']}"
Tone: {v['tone']}. Copy written by: {v['copy_author']}.

Staff notes (these win):
{_notes(site)}

The site went up in {m['era']['build_year']}. Later changes:
{_edits(m)}

Content density: {m['structure']['density']}.

Site plan (write every section, in this order):
{_plan_lines(site_pages(site))}
"""


def build_brief(site: Site, content_md: str) -> str:
    m, plan = site.manifest(), site.plan()
    era, v, a = m["era"], m["visual"], m["authorship"]
    h, p = a["habits"], v["palette"]
    lay, shape = v["layout"], v["shape"]
    pages = ", ".join(page_file(pg["id"]) for pg in site_pages(site))
    return f"""You are {_a(a['author'])} ({a['background']}). Design skill: {a['design_skill']}. Code skill: \
{a['code_skill']}. You're building the site for {plan['wordmark']} ({site.domain}), which \
{_clause(plan['business']['what_they_do'])}, in {era['build_year']}.

Your habits:
- naming: {h['naming']}
- units: {h['units']}
- reset: {h['reset']}
- media queries: {h['media_queries']}
- files: {h['files']}
- markup: {h['markup']}
- JavaScript: {h['js']}
- icons: {h['icons']}
- comments: {a['comments']}
- finish: {a['imperfection']}

Era: built in {era['build_year']}.
Available then: {_list(era['tech_available'])}.
Not available yet (do not use in the original build): {_list(era['tech_not_yet']) if era['tech_not_yet'] else 'nothing excluded'}.
Later edits to layer on top, each in its own year's style:
{_edits(m)}

The look: {v['direction']}: {v['direction_notes']}.
Palette ({p['family']}, {p['mode']} mode): background {p['bg']}, surface {p['surface']}, text {p['text']}, \
primary {p['primary']}, accent {p['accent']}, muted {p['muted']}, borders {p['border']}.
Fonts: headings {v['fonts']['heading']}; body {v['fonts']['body']}. Base size {v['base_size']}, type scale {v['type_scale']}.
Layout: width {lay['width']}; nav {lay['nav']}; grid {lay['grid']}; footer {lay['footer']}.
Shape: radius {shape['radius']}; shadows {shape['shadow']}; borders {shape['borders']}; spacing {shape['density']}.
Motion: {v['motion']}.
Graphics: {_list(v['graphics'])}.
Wordmark: {plan['wordmark']}.

Pages to produce: {pages}.

The copy, ready to use:

{content_md}
"""

# =============================================================================
# API HELPERS
# =============================================================================

_client = None
_client_lock = threading.Lock()


def client():
    global _client
    with _client_lock:
        if _client is None:
            try:
                import anthropic
            except ImportError:
                sys.exit("The anthropic package is needed: pip install anthropic")
            _client = anthropic.Anthropic(max_retries=4)
        return _client


def step_config(step: str):
    return {
        "assess": (ASSESS_MODEL, ASSESS_EFFORT, ASSESS_MAX_TOKENS, ASSESS_SYSTEM, ASSESS_SCHEMA),
        "plan": (PLAN_MODEL, PLAN_EFFORT, PLAN_MAX_TOKENS, PLAN_SYSTEM, PLAN_SCHEMA),
        "content": (CONTENT_MODEL, CONTENT_EFFORT, CONTENT_MAX_TOKENS, CONTENT_SYSTEM, None),
        "build": (BUILD_MODEL, BUILD_EFFORT, BUILD_MAX_TOKENS, BUILD_SYSTEM, None),
    }[step]


def user_prompt(step: str, site: Site) -> str:
    if step == "assess":
        return assess_brief(site)
    if step == "plan":
        return plan_brief(site)
    if step == "content":
        return content_brief(site)
    return build_brief(site, site.content())


def request_params(step: str, site: Site, user: str | None = None) -> dict:
    model, effort, max_tokens, system, schema = step_config(step)
    output_config = {"effort": effort}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    return {
        "model": model,
        "max_tokens": max_tokens,
        "output_config": output_config,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user if user is not None else user_prompt(step, site)}],
    }


def call(params: dict):
    """One streamed call. Returns (text, usage, stop_reason)."""
    c = client()
    if USE_FALLBACKS and params["model"] in FALLBACK_MODELS:
        stream = c.beta.messages.stream(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params)
    else:
        stream = c.messages.stream(**params)
    with stream as s:
        msg = s.get_final_message()
    text = "".join(b.text for b in msg.content if b.type == "text")
    return text, usage_dict(msg.usage, msg.model), msg.stop_reason


def usage_dict(u, model) -> dict:
    return {
        "model": model,
        "input": getattr(u, "input_tokens", 0) or 0,
        "output": getattr(u, "output_tokens", 0) or 0,
        "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
        "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
    }


def cost(u: dict, batch=False) -> float:
    pin, pout, pcw, pcr = PRICES.get(u["model"], PRICES["claude-opus-5-5"])
    c = (u["input"] * pin + u["output"] * pout + u["cache_write"] * pcw + u["cache_read"] * pcr) / 1e6
    return c * (BATCH_DISCOUNT if batch else 1)


def fmt_usage(u: dict, batch=False) -> str:
    return (f"in {u['input']:,} / out {u['output']:,} / cache w {u['cache_write']:,} r {u['cache_read']:,}"
            f"  ${cost(u, batch):.3f}")

# =============================================================================
# OUTPUT CHECKS (each returns what gets saved, or raises BadOutput)
# =============================================================================

FILE_RE = re.compile(r"^=== FILE: (.+?) ===\s*\n(.*?)\n=== END FILE ===\s*$", re.S | re.M)
ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def page_file(pid: str) -> str:
    return "index.html" if pid == "index" else f"{pid}.html"


def _json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise BadOutput(f"not valid JSON: {e}")


def check_assess(text: str, site: Site) -> dict:
    a = _json(text)
    a["earliest_year"] = max(2012, min(2026, int(a["earliest_year"])))
    a["moods"] = a["moods"][:3]
    return a


def check_plan(text: str, site: Site) -> dict:
    plan = _json(text)
    pages = plan["pages"]
    if not pages or pages[0]["id"] != "index":
        raise BadOutput("the first page must have id 'index'")
    if len(pages) > 8:
        raise BadOutput(f"{len(pages)} pages; 8 at most")
    seen_pages, seen_secs = set(), set()
    for p in pages:
        if not ID_RE.fullmatch(p["id"]) or p["id"] in seen_pages:
            raise BadOutput(f"bad or repeated page id {p['id']!r}")
        seen_pages.add(p["id"])
        if not p["sections"]:
            raise BadOutput(f"page {p['id']} has no sections")
        for s in p["sections"]:
            if not ID_RE.fullmatch(s["id"]) or s["id"] in seen_secs:
                raise BadOutput(f"bad or repeated section id {s['id']!r}")
            seen_secs.add(s["id"])
        if not p.get("added_in"):
            p.pop("added_in", None)
    b = plan["business"]
    year = site.manifest()["era"]["build_year"]
    b["founded"] = min(int(b["founded"]), year)
    b["team_size"] = max(1, int(b["team_size"]))
    if not b["offerings"]:
        raise BadOutput("no offerings")
    return plan


def check_content(text: str, site: Site) -> str:
    text = text.strip()
    for p in site_pages(site):
        if not re.search(rf"^## page: {re.escape(p['id'])}\b", text, re.M):
            raise BadOutput(f"content.md is missing page {p['id']}")
    return text + "\n"


def check_build(text: str, site: Site) -> dict[str, str]:
    files = {}
    for path, body in FILE_RE.findall(text):
        path = path.strip().lstrip("/")
        pp = PurePosixPath(path)
        if not path or ".." in pp.parts or pp.suffix.lower() not in ALLOWED_EXT or pp.name == DONE_FILE:
            raise BadOutput(f"bad file path: {path!r}")
        files[str(pp)] = body
    if not files:
        raise BadOutput("no files found in output")
    missing = {page_file(p["id"]) for p in site_pages(site)} - set(files)
    if missing:
        raise BadOutput(f"missing pages: {', '.join(sorted(missing))}")
    return files


CHECKS = {"assess": check_assess, "plan": check_plan, "content": check_content, "build": check_build}


def save_result(step: str, site: Site, result):
    if step == "assess":
        site.state["assess"] = result
        site.save()
    elif step == "plan":
        site.plan_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        site.state.pop("plan_ok", None)
        site.save()
    elif step == "content":
        site.content_path.write_text(result, encoding="utf-8")
    else:
        for rel, body in result.items():
            f = site.dir / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(body.rstrip("\n") + "\n", encoding="utf-8")
        log = {"domain": site.domain, "theme": site.state["theme"], "manifest": site.state.get("manifest"),
               "files": sorted(result), "finished": now(), "cost": round(site.total_cost(), 4)}
        (site.dir / DONE_FILE).write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")

# =============================================================================
# INTERACTION (short and prompted; --yes skips all of it)
# =============================================================================

_print_lock = threading.Lock()
_input_lock = threading.Lock()


def say(*a):
    with _print_lock:
        print(*a, flush=True)


def ask(label: str) -> str:
    try:
        return input(f"  {label}: ").strip()
    except EOFError:
        return ""


def ask_notes(site: Site):
    print(f"\n{site.domain}: \"{site.state['theme']}\"")
    print("  A few optional questions. Press Enter to skip any of them.")
    notes = {
        "offer": ask("Anything specific they do or offer"),
        "tone": ask("Tone or voice notes"),
        "mention": ask("Anything to mention or avoid"),
    }
    site.state["notes"] = {k: v for k, v in notes.items() if v}
    site.state["asked"] = True
    site.save()


def plan_summary(site: Site) -> str:
    plan, a = site.plan(), site.state["assess"]
    b = plan["business"]
    lines = [f"  {plan['wordmark']} ({site.domain}) · {b['business_type']} · since {b['founded']} · team of {b['team_size']}",
             f"  what: {b['what_they_do']} For {b['audience']}.",
             "  offers: " + "; ".join(o["name"] for o in b["offerings"]),
             "  quirks: " + "; ".join(b["quirks"]),
             f"  theme plausible from {a['earliest_year']}; moods {', '.join(a['moods'])}",
             "  pages:"]
    for p in plan["pages"]:
        added = f" [added {p['added_in']}]" if p.get("added_in") else ""
        lines.append(f"    {page_file(p['id'])}{added}: " + ", ".join(s["id"] for s in p["sections"]))
    if not any(p["id"] == ERROR_PAGE_ID for p in plan["pages"]):
        lines.append(f"    {page_file(ERROR_PAGE_ID)}: always added, in the site's voice and style")
    return "\n".join(lines)


def confirm_plan(site: Site) -> bool:
    """True to carry on, False to make a new plan. Raises Stop to stop."""
    with _input_lock:
        print(f"\n{site.domain}: the plan\n{plan_summary(site)}")
        while True:
            ans = ask(f"Enter to write the site, e to edit {site.plan_path} first, r for a new plan, q to stop").lower()
            if ans == "":
                site.state["plan_ok"] = True
                site.save()
                return True
            if ans == "e":
                input(f"  edit {site.plan_path}, save it, then press Enter... ")
                try:
                    check_plan(site.plan_path.read_text(encoding="utf-8"), site)
                except (BadOutput, KeyError, TypeError, ValueError) as e:
                    print(f"  that plan has a problem ({e}); fix it and try again")
                    continue
                print(plan_summary(site))
            elif ans == "r":
                site.plan_path.unlink()
                return False
            elif ans == "q":
                raise Stop(f"stopped at the plan; run again with --domain {site.domain} to carry on")

# =============================================================================
# DIRECT MODE
# =============================================================================


def run_step(step: str, site: Site):
    """Call with retries on unusable output, then save. Returns the last usage."""
    last = None
    for attempt in range(1, ATTEMPTS + 1):
        text, u, stop = call(request_params(step, site))
        site.add_usage(step, [u])
        try:
            if stop == "refusal":
                raise BadOutput("model refused")
            if stop == "max_tokens":
                raise BadOutput("output hit max_tokens")
            save_result(step, site, CHECKS[step](text, site))
            return u
        except (BadOutput, KeyError, TypeError, ValueError) as e:
            last = e
            say(f"    {site.domain}: {step} attempt {attempt} unusable: {e}")
    raise BadOutput(f"{step} failed after {ATTEMPTS} attempts: {last}")


def do_site(site: Site, interactive: bool, review: bool, force: bool) -> Site:
    if interactive and not site.state.get("asked") and "assess" not in site.state:
        ask_notes(site)
    while (step := site.next_step()):
        if step == "pick":
            pick_manifest(site, interactive, force)
            continue
        if step == "content" and interactive and not site.state.get("plan_ok"):
            if not confirm_plan(site):
                continue  # plan removed; make a new one
        u = run_step(step, site)
        say(f"  {site.domain}: {step} done ({fmt_usage(u)})")
        if step == "content" and review:
            with _input_lock:
                input(f"  review {site.content_path} now; press Enter to build it... ")
    say(f"  {site.domain}: built in {site.dir}/; site total ${site.total_cost():.3f}")
    return site


def run_direct(sites, interactive, review, force, jobs):
    done, failed = 0, []
    with ThreadPoolExecutor(max_workers=1 if (interactive or review) else jobs) as ex:
        futs = {ex.submit(do_site, s, interactive, review, force): s for s in sites}
        for f in as_completed(futs):
            s = futs[f]
            try:
                f.result()
                done += 1
            except Stop as e:
                failed.append(s)
                say(f"  {s.domain}: {e}")
            except Exception as e:  # keep the run going; the site stays incomplete and is retried next run
                failed.append(s)
                say(f"  {s.domain}: FAILED: {type(e).__name__}: {e}")
    return done, failed

# =============================================================================
# BATCH MODE (big lists: each step runs as one batch across every site that needs it)
# =============================================================================


def run_batch_step(step, sites, state_path: Path):
    """Returns {domain: (text, usage, stop)}."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    c = client()
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    key = f"{step}_batch"
    ids = {hashlib.sha256(s.domain.encode()).hexdigest()[:32]: s.domain for s in sites}
    if key in state and state.get(f"{step}_ids") == ids:
        batch_id = state[key]
        print(f"  resuming {step} batch {batch_id}")
    else:
        reqs = [Request(custom_id=cid, params=MessageCreateParamsNonStreaming(**request_params(step, Site(d))))
                for cid, d in ids.items()]
        batch_id = c.messages.batches.create(requests=reqs).id
        state[key], state[f"{step}_ids"] = batch_id, ids
        state_path.write_text(json.dumps(state, indent=2))
        print(f"  submitted {step} batch {batch_id} ({len(reqs)} requests)")
    while True:
        b = c.messages.batches.retrieve(batch_id)
        if b.processing_status == "ended":
            break
        rc = b.request_counts
        print(f"    {step}: processing {rc.processing}, done {rc.succeeded}, errored {rc.errored}", flush=True)
        time.sleep(BATCH_POLL_SECONDS)
    out = {}
    for r in c.messages.batches.results(batch_id):
        d = ids.get(r.custom_id)
        if r.result.type == "succeeded" and d:
            msg = r.result.message
            text = "".join(bl.text for bl in msg.content if bl.type == "text")
            out[d] = (text, usage_dict(msg.usage, msg.model), msg.stop_reason)
        else:
            print(f"    {d or r.custom_id}: {r.result.type}")
    state.pop(key, None)
    state.pop(f"{step}_ids", None)
    state_path.write_text(json.dumps(state, indent=2))
    return out


def run_batch(sites, review, force):
    state_path = OUT_DIR / ".batch-state.json"
    for step in STEPS:
        for s in sites:
            if s.next_step() == "pick":
                try:
                    pick_manifest(s, False, force)
                except (Stop, SystemExit) as e:
                    print(f"    {s.domain}: {e}")
        if step == "content" and review:
            input(f"  plans written under {OUT_DIR}/*/plan.json; edit any of them, then press Enter... ")
        if step == "build" and review:
            input(f"  copy written under {OUT_DIR}/*/content.md; edit any of it, then press Enter to build... ")
        need = [s for s in sites if s.next_step() == step]
        if not need:
            continue
        res = run_batch_step(step, need, state_path)
        for s in need:
            if s.domain not in res:
                continue
            text, u, stop = res[s.domain]
            s.add_usage(step, [u], batch=True)
            try:
                if stop != "end_turn":
                    raise BadOutput(f"stop reason {stop}")
                save_result(step, s, CHECKS[step](text, s))
            except (BadOutput, KeyError, TypeError, ValueError) as e:
                print(f"    {s.domain}: {step} unusable ({e}); will retry next run")
    done = [s for s in sites if s.complete()]
    return len(done), [s for s in sites if not s.complete()]

# =============================================================================
# DRY RUN
# =============================================================================


def est_tokens(s: str) -> int:
    return max(1, len(s) // 4)


def dry_run(sites):
    total = 0.0
    for n, s in enumerate(sites):
        # stand-ins for what the earlier steps would produce
        fake_assess = {"earliest_year": 2012, "business_type": "family business", "moods": ["warm"]}
        mp = Path(s.state["requested_manifest"]) if s.state.get("requested_manifest") else next(
            (p for p in sorted(MANIFEST_DIR.glob("*.json")) if not (used_dir() / p.name).exists()
             and is_persona(json.loads(p.read_text(encoding="utf-8")))), None)
        if not mp:
            sys.exit(f"no unused manifests in {MANIFEST_DIR}/; run manifest.py first")
        m = json.loads(mp.read_text(encoding="utf-8"))
        n_pages = {"one page": 1, "2-3 pages": 3, "3-5 pages": 4, "5-8 pages": 6}.get(m["structure"]["pages"], 4)
        n_secs = n_pages * 4
        prompts = {
            "assess": ASSESS_SYSTEM + assess_brief(s),
            "plan": PLAN_SYSTEM + plan_brief_for(s, m, fake_assess),
            "content": CONTENT_SYSTEM + "x" * (2500 + 200 * n_secs),
            "build": BUILD_SYSTEM + "x" * (6000 + 4 * (250 + 180 * n_secs)),
        }
        outs = {"assess": 60, "plan": 300 + 90 * n_secs, "content": 250 + 180 * n_secs, "build": 6000 + 2500 * (n_pages + 1)}
        site_cost = 0.0
        for step in STEPS:
            u = {"model": step_config(step)[0], "input": est_tokens(prompts[step]), "output": outs[step] * 2,
                 "cache_write": 0, "cache_read": 0}
            site_cost += cost(u)  # output x2 allows for thinking tokens
        total += site_cost
        if n == 0:
            for title, text in [("ASSESS SYSTEM", ASSESS_SYSTEM), (f"ASSESS USER ({s.domain})", assess_brief(s)),
                                ("PLAN SYSTEM", PLAN_SYSTEM),
                                (f"PLAN USER ({s.domain}, {mp.name}, stand-in assessment)", plan_brief_for(s, m, fake_assess)),
                                ("CONTENT SYSTEM", CONTENT_SYSTEM), ("BUILD SYSTEM", BUILD_SYSTEM)]:
                print("=" * 78, f"\n{title}\n" + "=" * 78)
                print(text)
            print("=" * 78)
        print(f"  {s.domain}: ~{n_pages} pages, ~{n_secs} sections; ~${site_cost:.2f}")
    print(f"estimate for {len(sites)} site(s): ~${total:.2f} direct, ~${total * BATCH_DISCOUNT:.2f} with the Batch API "
          "(rough: chars/4, thinking allowance included, no caching)")


def plan_brief_for(site: Site, m: dict, assess: dict) -> str:
    """plan_brief without needing the manifest copied in (for --dry-run)."""
    class _S:  # a minimal stand-in exposing what plan_brief reads
        pass
    s = _S()
    s.domain, s.state = site.domain, dict(site.state, assess=assess)
    s.manifest = lambda: m
    return plan_brief(s)

# =============================================================================
# MAIN
# =============================================================================


def read_list(path: Path):
    """One site per line: domain | theme | notes (notes optional). # starts a comment."""
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.split("#", 1)[0].strip() if not line.lstrip().startswith("#") else ""
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2 and not Site(parts[0]).exists():
            sys.exit(f"{path}:{n}: expected 'domain | theme | notes'")
        out.append((parts[0], parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else ""))
    return out


def main(argv=None):
    global OUT_DIR, MANIFEST_DIR
    ap = argparse.ArgumentParser(description="Build a website from a domain name, a theme and a persona manifest.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--domain", help="the site's domain name")
    src.add_argument("--list", type=Path, help="a file of sites, one 'domain | theme | notes' per line")
    ap.add_argument("--theme", help="a short description of the business or project")
    ap.add_argument("--notes", help="anything staff want the copy to include or avoid")
    ap.add_argument("--manifest", type=Path, help="use this manifest instead of picking one")
    ap.add_argument("--yes", action="store_true", help="no questions and no plan check")
    ap.add_argument("--force", action="store_true", help="use --manifest even if it doesn't fit the theme")
    ap.add_argument("--review", action="store_true", help="also pause after the copy so it can be edited")
    ap.add_argument("--dry-run", action="store_true", help="show prompts and a cost estimate; no API calls")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--batch", action="store_true", help="use the Batch API (50%% cheaper, slower)")
    mode.add_argument("--no-batch", action="store_true", help="never use the Batch API")
    ap.add_argument("--jobs", type=int, default=JOBS, help=f"sites at once in direct mode (default {JOBS})")
    ap.add_argument("--out", type=Path, default=OUT_DIR, help=f"output folder (default {OUT_DIR}/)")
    ap.add_argument("--manifests", type=Path, default=MANIFEST_DIR, help=f"manifest folder (default {MANIFEST_DIR}/)")
    args = ap.parse_args(argv)
    OUT_DIR, MANIFEST_DIR = args.out, args.manifests
    if args.list and args.manifest:
        ap.error("--manifest only works with --domain")

    if args.dry_run:
        specs = [(args.domain, args.theme or "(theme)", args.notes)] if args.domain else read_list(args.list)
        sites = []
        for d, t, n in specs:
            s = Site(d)
            if not s.exists():
                s.state = {"domain": s.domain, "theme": t, "notes": {"mention": n} if n else {}, **roll(s.domain),
                           "requested_manifest": str(args.manifest) if args.manifest else None}
            sites.append(s)
        dry_run(sites)
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.manifest:
        check_requested(args.manifest, args.domain)
    if args.domain:
        theme = args.theme
        if not theme and not Site(args.domain).exists():
            if args.yes or not sys.stdin.isatty():
                ap.error("--theme is needed for a new site")
            theme = ask("Theme (a short description of the business)")
        sites = [new_site(args.domain, theme, {"mention": args.notes} if args.notes else {}, args.manifest)]
    else:
        sites = [new_site(d, t, {"mention": n} if n else {}) for d, t, n in read_list(args.list)]

    pending = [s for s in sites if not s.complete()]
    print(f"{len(sites)} site(s); {len(sites) - len(pending)} already built; {len(pending)} to do.")
    if not pending:
        return 0
    interactive = bool(args.domain) and not args.yes and sys.stdin.isatty()
    use_batch = bool(args.list) and (args.batch or (not args.no_batch and len(pending) >= BATCH_THRESHOLD))
    t0 = time.time()
    if use_batch:
        print(f"using the Batch API for {len(pending)} sites (each step usually finishes within the hour)")
        done, failed = run_batch(pending, args.review, args.force)
    else:
        done, failed = run_direct(pending, interactive, args.review, args.force, args.jobs)
    grand = sum(s.total_cost() for s in pending)
    print(f"done: {done} built, {len(failed)} not finished, ${grand:.2f} spent on these sites so far, "
          f"{time.time() - t0:.0f}s.")
    if failed:
        print("run the same command again to carry on.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
