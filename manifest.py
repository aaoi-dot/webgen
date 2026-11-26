#!/usr/bin/env python3
"""manifest.py: write unique persona manifests (JSON) for site.py.

    python manifest.py --count 25
    python manifest.py --count 25 --out other/
    python manifest.py --count 25 --years 2015-2020
    python manifest.py --capacity

A manifest says who built a site and how: the author and their habits, the site's voice, the era it
was built in, the visual design and the author's structural habits. It says nothing about what the
site is for. The domain name and theme are given to site.py when a site is made, so any manifest can
become any kind of site. Each manifest is used for one site only.

Uniqueness is enforced against every manifest already in the output folder;
no uniqueness data is written into the manifests themselves.
"""

from __future__ import annotations

import argparse
import colorsys
import hashlib
import json
import os
import random
import re
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path

CAP = 100_000
YEAR_MIN, YEAR_MAX = 2012, 2026

# Era bands used by the core signature.
BANDS = [(2012, 2014), (2015, 2017), (2018, 2020), (2021, 2023), (2024, 2026)]

# =============================================================================
# LIBRARY DATA (drafted; review and correct freely)
# =============================================================================

# --- Business types -----------------------------------------------------------
# Not chosen here: site.py works out the business type from the theme. Each manifest lists the
# types its author plausibly builds for ("suits"), so site.py can pick a fitting manifest.
BUSINESS_TYPES = [
    "solo founder", "freelancer", "boutique studio", "agency", "consultancy", "family business",
    "small local firm", "seed-stage startup", "bootstrapped SaaS", "venture-backed scale-up",
    "enterprise product team", "open-source project", "research spin-out",
]

# Authors who would plausibly not build this kind of organisation's site.
BTYPE_EXCLUDES_AUTHORS = {
    "agency": {"founder-engineer", "enterprise web team developer", "backend specialist doing the front end"},
    "boutique studio": {"founder-engineer", "enterprise web team developer"},
    "freelancer": {"founder-engineer", "enterprise web team developer", "in-house product designer", "marketing generalist"},
    "solo founder": {"enterprise web team developer", "in-house product designer"},
    "family business": {"enterprise web team developer", "in-house product designer", "developer-relations engineer"},
    "small local firm": {"enterprise web team developer", "in-house product designer", "developer-relations engineer"},
    "seed-stage startup": {"enterprise web team developer"},
    "bootstrapped SaaS": {"enterprise web team developer"},
    "research spin-out": {"enterprise web team developer"},
    "open-source project": {"enterprise web team developer", "marketing generalist"},
    "enterprise product team": {"founder-engineer"},
}

# --- Moods (shared vocabulary for coherence) -----------------------------------
# warm playful sombre professional technical luxury energetic community clinical creative calm earthy
# A manifest's "taste" is the moods its design direction and palette share. site.py prefers a manifest
# whose taste overlaps the theme's moods.

# --- Tones & voice ----------------------------------------------------------
TONES = ["formal", "warm", "terse", "enthusiastic", "dry", "earnest", "chatty", "plain", "reassuring", "proud"]

TONE_FOR_MOOD = {
    "sombre": ["formal", "reassuring", "plain", "earnest"],
    "professional": ["formal", "plain", "reassuring", "terse", "proud"],
    "clinical": ["reassuring", "plain", "formal", "warm"],
    "technical": ["terse", "plain", "dry", "enthusiastic"],
    "luxury": ["proud", "formal", "warm"],
    "warm": ["warm", "chatty", "dry", "earnest", "proud"],
    "earthy": ["warm", "earnest", "plain", "dry"],
    "playful": ["enthusiastic", "chatty", "warm", "dry"],
    "energetic": ["enthusiastic", "terse", "chatty", "proud"],
    "community": ["warm", "earnest", "chatty", "plain"],
    "creative": ["dry", "earnest", "chatty", "terse", "enthusiastic"],
    "calm": ["warm", "reassuring", "plain", "earnest"],
}

PERSONALITIES = {
    "formal": ["courteous, precise, enterprise-ready", "measured and correct, never chummy",
               "polite, thorough, fond of full sentences"],
    "warm": ["warm, wry, plain-spoken", "kind and a bit scatty", "friendly, generous with detail"],
    "terse": ["short sentences, no fuss", "practical, allergic to waffle", "busy and to the point, like a commit message"],
    "enthusiastic": ["excitable, exclamation-prone", "bubbly and proud of every release",
                     "energetic, talks in lists"],
    "dry": ["deadpan, self-deprecating", "dry humour, understatement", "sardonic but caring"],
    "earnest": ["sincere, a bit idealistic", "earnest, explains the why", "values-driven, careful with claims"],
    "chatty": ["chatty, goes off on tangents", "chatty, loves an aside", "talks to readers like teammates"],
    "plain": ["plain and factual", "no-nonsense and clear", "matter-of-fact, quietly confident"],
    "reassuring": ["calm, patient, reassuring", "gentle and steady", "careful, puts nervous buyers at ease"],
    "proud": ["proud of the engineering, a touch grand", "confident, benchmark-minded", "quietly boastful about quality"],
}

VOICE_SAMPLES = {
    "formal": ["We take that responsibility seriously, and always have.",
               "Our team is available to discuss your requirements in detail.",
               "Please do not hesitate to contact us should you require further information."],
    "warm": ["We built this because we needed it ourselves.", "We're a small team and we read every message.",
             "Honestly, start with the first thing on the list. It's the bit we're proudest of."],
    "terse": ["Fast. Documented. Done.", "No sales call needed. Sign up and ship.",
              "It works. Read the docs."],
    "enthusiastic": ["We LOVE shipping and it shows!", "Something new just landed and we're so excited!",
                     "Try it now, we can't wait to see what you build!"],
    "dry": ["We fixed the bug. Then we fixed the fix.", "Yes, we've heard the joke about the name.",
            "Started small. Since then: mostly the same, but with better uptime."],
    "earnest": ["We started this because teams deserved better tools.", "Everything we build starts with the people who use it.",
                "We'd rather do fewer things and do them well."],
    "chatty": ["Right, so, you've found us! Let me tell you a bit about what we do...",
               "If you've used us before you'll know release week is always a bit chaotic.",
               "Our designer says I write too much copy, but here goes."],
    "plain": ["We do a few things. Details below.", "One product. A few plans. Clear limits.",
              "If you have a question, just ask."],
    "reassuring": ["There's no such thing as a silly question here.", "Take your time. The free plan doesn't expire.",
                   "Whatever you're migrating from, we'll talk it through first."],
    "proud": ["Built by engineers who've done this at scale.", "People switch to us for the details, and we can see why.",
              "We've never shipped a breaking change without warning."],
}

COPY_AUTHORS = {
    "founder": 5, "a product marketer": 3, "a professional copywriter": 2, "the developer who built it": 2,
    "the web author's filler, edited by the founder": 2, "a docs writer": 1,
}


# --- Era feature timeline (approximate) ----------------------------------------
TECH_TIMELINE = [
    (2012, ["@font-face web fonts", "media queries", "box-shadow and border-radius", "CSS gradients",
            "CSS transitions", "jQuery", "HTML5 semantic elements", "icon fonts", "rgba() colours", "float layouts",
            "Google Fonts", "box-sizing: border-box"]),
    (2013, ["rem units", "CSS animations and keyframes"]),
    (2015, ["flexbox", "inline SVG icons", "SVG sprites", "calc()", "vw/vh units"]),
    (2016, ["ES2015 (let/const, arrow functions)", "srcset/picture", "system font stack"]),
    (2017, ["CSS grid", "custom properties (CSS variables)", "fetch()", "object-fit", "ES modules in browsers (late 2017)"]),
    (2019, ["position: sticky", "IntersectionObserver", "prefers-color-scheme", "loading=\"lazy\"", "prefers-reduced-motion",
            "scroll-behavior: smooth"]),
    (2020, ["clamp(), min(), max()", "flexbox gap", ":focus-visible (partial)"]),
    (2021, ["aspect-ratio", ":is() and :where()", "logical properties widely used", "variable fonts common"]),
    (2022, ["<dialog>", "accent-color", "cascade layers (@layer)"]),
    (2023, ["container queries", ":has()", "native CSS nesting (late 2023)", "dvh/svh units", "oklch()",
            "text-wrap: balance", "subgrid"]),
    (2024, ["popover attribute", "@starting-style", "scroll-driven animations (Chromium)", "light-dark()"]),
    (2025, ["view transitions across pages (partial)", "@scope", "anchor positioning (partial)"]),
]

# --- Authors ------------------------------------------------------------------
# design / code: skill ranges 1 (low) .. 6 (very high). era: year weight multipliers by band index.
SKILL_WORDS = {1: "low", 2: "low-mid", 3: "mid", 4: "mid-high", 5: "high", 6: "very high"}

AUTHORS = {
    "professional web designer": dict(
        design=(5, 6), code=(4, 5), era=(1, 1, 1, 1, 1), bg="works at a design-led studio; considered type and spacing, a real design system",
        naming="bem:3 semantic:3 abbrev:1", units="px:2 em:2 rem:3 mixed:2", reset="normalize:4 meyer:1 modern:3",
        media="desktop:2 mobile:4 bolted:1", files="one:3 several:3 one_print:1", markup="semantic:4 wrappers:3",
        js="none:2 jquery:2 vanilla:4 data:3 modules:1", icons="inline_svg:4 sprite:3 svg_files:2 icon_font:2 css:1",
        comments="banner:4 sparse:3 todo:1 credit:2", imperfection="polished:5 normal:3"),
    "competent freelancer": dict(
        design=(4, 5), code=(4, 5), era=(1, 1, 1, 1, 1), bg="freelances for startups and agencies; reuses their own starter files site to site",
        naming="semantic:4 bem:2 abbrev:2 utility:1", units="px:4 em:2 rem:3 mixed:2", reset="normalize:4 meyer:2 modern:2 boxsizing:2",
        media="desktop:3 mobile:3 bolted:1", files="one:4 several:2 one_print:1", markup="semantic:3 wrappers:4",
        js="none:2 jquery:4 vanilla:3 data:2", icons="icon_font:3 inline_svg:3 svg_files:2 sprite:1",
        comments="banner:4 sparse:3 todo:2 credit:2 leftover:1", imperfection="polished:2 normal:5 rough:1"),
    "graphic designer who moved to the web": dict(
        design=(5, 6), code=(2, 3), era=(1.3, 1.1, 1, 0.9, 0.8), bg="trained in print; thinks in artboards, fights the box model",
        naming="mixed:4 semantic:2 abbrev:2 ids:2", units="px:6 mixed:2 em:1", reset="none:2 meyer:3 normalize:2 boxsizing:1",
        media="desktop:4 bolted:4 none:2", files="one:4 several:2 inline:1", markup="wrappers:4 divsoup:4 semantic:1",
        js="none:3 jquery:4 inline:2 vanilla:1", icons="svg_files:4 inline_svg:2 css:2 icon_font:2",
        comments="sparse:3 none:2 leftover:3 apologetic:2", imperfection="normal:4 rough:4"),
    "backend specialist doing the front end": dict(
        design=(1, 2), code=(4, 5), era=(1, 1, 1, 1, 1), bg="a backend developer; tidy logic, default-ish styling, likes tables and forms",
        naming="semantic:3 ids:1 mixed:2 utility:2", units="px:4 em:2 rem:2", reset="none:4 normalize:2 boxsizing:2",
        media="none:3 bolted:4 mobile:1 desktop:1", files="one:5 inline:2", markup="semantic:2 wrappers:3 divsoup:1",
        js="vanilla:4 modules:3 data:2 jquery:2 none:1", icons="unicode:4 none:3 inline_svg:2",
        comments="sparse:3 todo:4 none:2 leftover:1", imperfection="normal:5 rough:3"),
    "junior developer or bootcamp graduate": dict(
        design=(2, 4), code=(3, 3), era=(0.6, 1, 1.2, 1.3, 1.2), bg="recently learned from tutorials; follows them closely, a bit over-engineered",
        naming="bem:3 mixed:4 semantic:2", units="rem:4 px:3 mixed:3", reset="modern:3 normalize:3 boxsizing:3",
        media="mobile:4 desktop:2 bolted:2", files="one:3 several:4", markup="semantic:3 wrappers:3 divsoup:2",
        js="vanilla:4 data:2 modules:3 jquery:1", icons="inline_svg:3 icon_font:3 svg_files:2",
        comments="chatty:4 todo:3 leftover:3 sparse:1", imperfection="normal:5 rough:3"),
    "enterprise web team developer": dict(
        design=(2, 4), code=(3, 4), era=(1.3, 1.1, 1, 0.9, 0.8), bg="on a corporate web team; follows a brand guideline PDF, lots of wrappers, long legal footer, cautious",
        naming="abbrev:2 semantic:3 bem:2 mixed:2 ids:1", units="px:5 em:2 rem:2 mixed:2", reset="normalize:4 meyer:3 boxsizing:1",
        media="desktop:5 bolted:2 mobile:1", files="several:4 one:2 one_print:1", markup="wrappers:6 divsoup:2 semantic:1",
        js="jquery:5 vanilla:2 data:1 inline:1", icons="icon_font:4 svg_files:3 sprite:2",
        comments="banner:5 leftover:3 todo:1 credit:1", imperfection="normal:5 polished:2 rough:1"),
    "accessibility-focused developer": dict(
        design=(3, 4), code=(5, 6), era=(0.7, 0.9, 1.1, 1.2, 1.3), bg="cares about semantics, keyboard use, contrast and proper ARIA; plain but very clear",
        naming="semantic:5 bem:3", units="em:4 rem:5", reset="normalize:4 modern:3 boxsizing:2",
        media="mobile:6 desktop:1", files="one:4 several:2 one_print:2", markup="semantic:9",
        js="none:4 vanilla:4 data:3 modules:1", icons="inline_svg:5 css:2 none:2",
        comments="sparse:3 banner:3 chatty:1 todo:1", imperfection="polished:5 normal:3"),
    "founder-engineer": dict(
        design=(1, 3), code=(4, 6), era=(0.8, 1, 1, 1.2, 1.3), bg="the technical co-founder; built it between product sprints, borrows patterns from docs sites, ships fast",
        naming="utility:3 semantic:3 mixed:2 bem:1", units="rem:3 px:4 em:1", reset="modern:3 normalize:2 boxsizing:3 none:1",
        media="mobile:3 bolted:4 desktop:1", files="one:6 several:1", markup="semantic:3 wrappers:3 divsoup:1",
        js="vanilla:4 modules:3 data:2 jquery:1", icons="inline_svg:4 unicode:2 svg_files:2",
        comments="todo:4 sparse:3 leftover:2 none:1", imperfection="normal:5 rough:3"),
    "front-end perfectionist": dict(
        design=(5, 6), code=(6, 6), era=(0.8, 1, 1, 1.1, 1.2), bg="minimal and precise; modern CSS as far as the era allows, well commented",
        naming="bem:4 semantic:4", units="rem:5 em:3 mixed:2", reset="modern:4 normalize:3 boxsizing:2",
        media="mobile:7", files="several:3 one:3 one_print:2", markup="semantic:8",
        js="none:3 vanilla:3 modules:4 data:3", icons="inline_svg:4 sprite:4 css:1",
        comments="banner:4 sparse:3 credit:1", imperfection="polished:9"),
    "marketing generalist": dict(
        design=(3, 4), code=(2, 3), era=(1, 1, 1, 1, 1), bg="runs growth and the website; big CTAs, A/B-test leftovers, copy-led, inconsistent styles",
        naming="mixed:5 semantic:2 utility:2", units="px:5 mixed:3 rem:1", reset="none:3 normalize:3 boxsizing:2",
        media="bolted:4 desktop:3 mobile:2", files="one:3 several:2 inline:3", markup="wrappers:4 divsoup:3 semantic:1",
        js="jquery:4 inline:3 vanilla:2 data:1", icons="icon_font:4 inline_svg:2 svg_files:2 unicode:1",
        comments="sparse:3 leftover:4 todo:2 none:2", imperfection="normal:5 rough:3"),
    "in-house product designer": dict(
        design=(5, 6), code=(3, 4), era=(0.6, 0.9, 1.1, 1.2, 1.3), bg="the product designer, who also owns the marketing site; design-tool first, pixel-careful, CSS a bit verbose",
        naming="bem:3 semantic:3 mixed:2 utility:1", units="px:4 rem:4 mixed:2", reset="modern:3 normalize:3 boxsizing:2",
        media="desktop:3 mobile:3 bolted:2", files="one:3 several:3", markup="wrappers:4 semantic:3 divsoup:1",
        js="vanilla:4 data:2 jquery:1 none:2 modules:1", icons="inline_svg:5 svg_files:3 sprite:1",
        comments="sparse:4 banner:2 leftover:2 apologetic:1", imperfection="polished:4 normal:4"),
    "developer-relations engineer": dict(
        design=(2, 4), code=(5, 6), era=(0.6, 0.9, 1.1, 1.2, 1.3), bg="a DevRel and docs engineer; thinks in code samples, copy buttons and quickstarts; tidy, plain, monospace accents",
        naming="semantic:5 bem:2 utility:2", units="rem:5 em:3 px:1", reset="modern:4 normalize:3 boxsizing:2",
        media="mobile:5 bolted:2", files="one:4 several:3", markup="semantic:7 wrappers:2",
        js="vanilla:4 modules:4 data:3 none:1", icons="inline_svg:5 unicode:2 css:1",
        comments="sparse:3 banner:2 chatty:2 todo:2", imperfection="polished:4 normal:5"),
}

# Habit options: key -> (text, first year, last year)
HABITS = {
    "naming": {
        "bem": ("BEM (block__element--modifier)", 2014, 2026),
        "semantic": ("semantic, hyphenated (.site-header, .opening-times)", 2012, 2026),
        "abbrev": ("abbreviated (.hdr, .nav-wrp, .ftr-col)", 2012, 2026),
        "mixed": ("mixed: some camelCase, some hyphens, drifts between pages", 2012, 2026),
        "ids": ("mostly #id selectors, few classes", 2012, 2026),
        "utility": ("component names plus a few home-made helpers (.left, .mt20, .clearfix)", 2012, 2026),
    },
    "units": {
        "px": ("px", 2012, 2026), "em": ("em", 2012, 2026), "rem": ("rem", 2013, 2026),
        "mixed": ("mixed: px for borders, em for type, % for widths", 2012, 2026),
    },
    "reset": {
        "none": ("none", 2012, 2026), "normalize": ("normalize-style", 2012, 2026),
        "meyer": ("Eric Meyer-style full reset", 2012, 2018),
        "custom_old": ("a home-made reset: * { margin: 0; padding: 0; }", 2012, 2026),
        "boxsizing": ("just a box-sizing: border-box rule", 2013, 2026),
        "modern": ("a short modern reset (box-sizing, margin, img max-width, font inherit)", 2019, 2026),
    },
    "media": {
        "desktop": ("desktop-first max-width breakpoints", 2012, 2026),
        "mobile": ("mobile-first min-width breakpoints", 2013, 2026),
        "none": ("none: fixed layout, zooms on phones", 2012, 2017),
        "bolted": ("a single breakpoint bolted on at the end of the stylesheet", 2012, 2026),
    },
    "files": {
        "one": ("one stylesheet (style.css)", 2012, 2026),
        "several": ("several stylesheets split by concern (layout.css, type.css, pages.css)", 2012, 2026),
        "inline": ("a <style> block in each page, copied and drifting between pages", 2012, 2026),
        "one_print": ("one stylesheet plus a small print.css", 2012, 2026),
    },
    "markup": {
        "semantic": ("semantic HTML5 elements throughout", 2012, 2026),
        "wrappers": ("mostly semantic, with wrapper and inner divs", 2012, 2026),
        "divsoup": ("nested-div heavy, few semantic elements", 2012, 2026),
    },
    "js": {
        "none": ("no JavaScript", 2012, 2026),
        "jquery": ("jQuery from a CDN for the nav toggle and small effects", 2012, 2021),
        "inline": ("inline onclick handlers", 2012, 2026),
        "vanilla": ("a separate script.js in plain JS", 2012, 2026),
        "data": ("plain JS wired up through data- attributes", 2014, 2026),
        "modules": ("ES modules split into small files", 2018, 2026),
    },
    "icons": {
        "inline_svg": ("inline SVG", 2015, 2026), "sprite": ("an SVG sprite with <use>", 2015, 2026),
        "svg_files": ("separate .svg files", 2013, 2026), "icon_font": ("an icon font (Font Awesome style)", 2012, 2022),
        "css": ("CSS shapes and pseudo-elements", 2012, 2026), "unicode": ("unicode characters and emoji", 2012, 2026),
        "none": ("no icons", 2012, 2026),
    },
}
COMMENTS = {
    "none": "none", "sparse": "sparse, only where something is odd",
    "banner": "banner dividers (/* ==== HEADER ==== */) with the odd TODO",
    "chatty": "chatty explanations, like notes to self", "apologetic": "apologetic ('sorry, this is a hack')",
    "todo": "TODOs with dates that never got done", "leftover": "leftover commented-out code",
    "credit": "a credit line in the header ('Site by ...')",
}
IMPERFECTION = {"polished": "polished", "normal": "normal", "rough": "a bit rough (still a working site)"}

# --- Design directions --------------------------------------------------------
# name: (first year, last year, design min, design max, moods (None=any), dark-only, description)
DIRECTIONS = {
    "skeuomorphic, textured": (2012, 2014, 2, 5, {"warm", "earthy", "playful", "luxury", "community", "creative"}, False,
        "linen and paper textures (CSS noise), stitched edges, embossed buttons, ribbon banners"),
    "glossy web 2.0 leftovers": (2012, 2014, 1, 3, {"playful", "energetic", "community", "technical", "warm", "professional"}, False,
        "glossy gradient buttons, reflections, rounded header bar, starburst badges"),
    "corporate clean with gradients": (2012, 2016, 2, 4, {"professional", "clinical", "technical", "community", "calm"}, False,
        "light gradient header, boxed content area, subtle drop shadows, a strapline under the logo"),
    "flat design, bold blocks": (2013, 2018, 3, 6, {"energetic", "playful", "technical", "creative", "community", "professional"}, False,
        "flat colour blocks, no shadows, bold section bands, simple geometric icons"),
    "flat, warm, illustration-led": (2014, 2020, 3, 6, {"warm", "playful", "earthy", "community", "creative", "calm"}, False,
        "flat colour with friendly SVG illustrations, rounded but not bubbly"),
    "long-shadow flat": (2013, 2015, 3, 5, {"playful", "energetic", "technical", "creative"}, False,
        "flat icons with long diagonal shadows, saturated circles"),
    "magazine editorial, serif-led": (2012, 2026, 4, 6, {"warm", "luxury", "creative", "calm", "sombre", "professional", "earthy"}, False,
        "big serif headings, generous measure, drop caps, pull quotes, columns"),
    "swiss grid, typographic": (2012, 2026, 4, 6, {"professional", "technical", "creative", "calm", "luxury"}, False,
        "strict grid, grotesk type, hairlines, asymmetry, very little colour"),
    "plain document style": (2012, 2026, 1, 3, None, False,
        "near-browser defaults, one column, content first, almost no decoration"),
    "dark and moody": (2012, 2026, 3, 6, {"luxury", "creative", "energetic", "sombre"}, True,
        "dark backgrounds, light type, one warm accent, atmospheric"),
    "full-width bands with ghost buttons": (2014, 2018, 3, 5, {"professional", "luxury", "creative", "energetic", "calm", "technical"}, False,
        "full-width colour bands, thin-outline buttons, centred headings, big intro band"),
    "material-inspired": (2015, 2019, 3, 5, {"technical", "professional", "energetic", "playful", "clinical"}, False,
        "paper-like layers, elevation shadows, a floating action button, bold primary colour"),
    "minimal whitespace, thin type": (2015, 2020, 4, 6, {"luxury", "calm", "creative", "professional", "clinical"}, False,
        "lots of white space, light-weight sans, small caps labels, hairline rules"),
    "brutalist, raw": (2017, 2026, 4, 6, {"creative", "technical", "energetic"}, False,
        "visible structure, system or mono type, harsh borders, deliberate awkwardness"),
    "bold gradients and blobs": (2018, 2022, 3, 6, {"energetic", "playful", "technical", "creative"}, False,
        "vivid duotone gradients, blob shapes, chunky rounded type"),
    "soft pastel, rounded": (2018, 2026, 3, 6, {"playful", "calm", "warm", "clinical", "community"}, False,
        "pastel fills, big radii, soft shadows, friendly sans"),
    "soft UI (neumorphism)": (2020, 2022, 4, 6, {"calm", "technical", "clinical"}, False,
        "same-colour extruded surfaces with paired light/dark shadows, low contrast kept readable"),
    "glassmorphism": (2020, 2023, 4, 6, {"technical", "energetic", "creative", "luxury"}, False,
        "frosted translucent panels over colourful gradient backgrounds"),
    "retro / Y2K revival": (2021, 2026, 4, 6, {"playful", "creative", "energetic"}, False,
        "chrome-ish type, pixel accents, bright clashing colour, sticker shapes"),
    "bento grid, product-style": (2023, 2026, 4, 6, {"technical", "professional", "creative", "energetic"}, False,
        "tiles of varied sizes, large type in panels, subtle borders"),
    "warm minimal, earthy neutrals": (2019, 2026, 3, 6, {"earthy", "calm", "warm", "luxury", "clinical"}, False,
        "sand and clay neutrals, serif display type, plenty of space, organic dividers"),
    "big type, editorial grotesk": (2019, 2026, 4, 6, {"creative", "professional", "energetic", "technical"}, False,
        "huge grotesk headings, tight leading, sparse colour, confident layout"),
    "functional, data-dense": (2012, 2026, 1, 4, {"technical", "professional", "community", "clinical"}, False,
        "tables, small type, compact spacing, few decorations"),
    "classic trustworthy": (2012, 2026, 2, 5, {"professional", "sombre", "clinical", "calm", "luxury", "community"}, False,
        "navy or burgundy, serif headings, conservative wordmark, ruled boxes"),
    "hand-drawn doodle": (2014, 2026, 3, 6, {"playful", "creative", "warm", "community"}, False,
        "wobbly SVG lines, handwritten accents, scribbled underlines"),
    "template-like startup theme": (2012, 2026, 1, 3, None, False,
        "looks like a bought landing-page theme: banner, three feature boxes, a stats row, footer links"),
    "organic shapes, nature": (2016, 2026, 3, 6, {"earthy", "calm", "warm", "clinical"}, False,
        "curved section edges, leaf and wave SVG shapes, muted greens"),
    "high-contrast accessible": (2012, 2026, 3, 5, None, False,
        "strong contrast, big targets, clear focus styles, plain layout"),
    "neon on black": (2015, 2026, 3, 6, {"energetic", "creative", "playful"}, True,
        "black background, glowing neon accents, condensed type, a bit gamer"),
    "dev-tools dark, code-first": (2016, 2026, 3, 6, {"technical", "creative", "energetic"}, True,
        "near-black background, monospace accents, a code block as the main hero element, syntax-colour accents"),
    "docs-site style": (2014, 2026, 2, 5, {"technical", "professional", "calm", "clinical", "community"}, False,
        "left sidebar of sections, generous code samples, callout boxes, a version badge in the header"),
    "SaaS gradient landing page": (2016, 2026, 3, 6, {"energetic", "technical", "playful", "creative", "professional"}, False,
        "soft linear or mesh gradients, a product screenshot drawn in CSS/SVG, pill buttons, a feature grid"),
    "enterprise solutions site": (2012, 2026, 2, 5, {"professional", "clinical", "calm", "luxury", "sombre"}, False,
        "wide header band with an abstract graphic, a mega-menu of 'solutions', conservative blue, lots of whitespace"),
    "dashboard-as-marketing": (2018, 2026, 4, 6, {"technical", "professional", "clinical", "energetic"}, False,
        "the page looks like the product: panels, SVG charts, metric tiles, dense UI chrome"),
    "AI-era aurora glow": (2022, 2026, 4, 6, {"technical", "creative", "energetic", "luxury"}, True,
        "dark canvas with blurred aurora glows, gradient-text headings, a glassy prompt-box UI, sparkle icons"),
    "playful creator tool": (2019, 2026, 3, 6, {"playful", "creative", "energetic"}, False,
        "chunky outlines, sticker-like UI mockups, bright fills, hand-drawn arrows"),
}

# --- Palette families -----------------------------------------------------------
# name: (primary hue range, primary sat range, accent hue (abs range or None for offset), moods, harmony)
PALETTES = {
    "terracotta & cream": ((12, 26), (0.45, 0.65), (38, 48), {"warm", "earthy", "creative", "community", "luxury"}, "analogous"),
    "navy & brass": ((210, 225), (0.45, 0.7), (38, 46), {"professional", "sombre", "luxury", "community", "calm"}, "complementary"),
    "forest greens": ((120, 150), (0.3, 0.55), (40, 55), {"earthy", "calm", "community", "clinical", "warm"}, "analogous"),
    "slate & teal": ((175, 195), (0.4, 0.65), (205, 220), {"technical", "clinical", "professional", "calm", "creative"}, "analogous"),
    "berry & blush": ((330, 350), (0.5, 0.7), (10, 25), {"playful", "warm", "luxury", "creative"}, "analogous"),
    "charcoal & citrus": ((60, 80), (0.75, 0.95), (25, 40), {"energetic", "technical", "creative"}, "split"),
    "ocean blues": ((195, 215), (0.5, 0.75), (180, 190), {"clinical", "calm", "professional", "community", "technical", "energetic"}, "analogous"),
    "mustard & olive": ((42, 52), (0.6, 0.8), (70, 90), {"earthy", "warm", "creative", "community"}, "analogous"),
    "plum & gold": ((280, 300), (0.35, 0.55), (40, 48), {"luxury", "sombre", "creative", "calm"}, "complementary"),
    "monochrome": ((0, 360), (0.0, 0.04), None, {"professional", "creative", "technical", "luxury", "sombre", "energetic"}, "mono"),
    "candy brights": ((0, 360), (0.7, 0.9), None, {"playful", "energetic", "community"}, "triadic"),
    "sage & stone": ((85, 110), (0.15, 0.3), (25, 35), {"calm", "earthy", "clinical", "sombre", "luxury", "warm"}, "analogous"),
    "red & black": ((352, 360), (0.65, 0.85), None, {"energetic", "creative", "professional", "community"}, "mono"),
    "sky & sunshine": ((195, 210), (0.6, 0.8), (42, 50), {"playful", "community", "warm", "energetic", "clinical"}, "complementary"),
    "lavender & grey": ((250, 270), (0.25, 0.45), (330, 345), {"calm", "clinical", "playful", "sombre", "creative"}, "analogous"),
    "burgundy & parchment": ((345, 355), (0.45, 0.65), (38, 45), {"sombre", "luxury", "professional", "warm", "community"}, "complementary"),
    "orange & teal": ((20, 32), (0.75, 0.9), (180, 190), {"energetic", "playful", "creative", "technical"}, "complementary"),
    "earthy browns": ((25, 35), (0.25, 0.45), (95, 115), {"earthy", "warm", "community", "sombre"}, "analogous"),
}

# --- Fonts ---------------------------------------------------------------------
# (heading, body, first year, kind, moods or None)
FONT_PAIRS = [
    ("Georgia, 'Times New Roman', serif", "Georgia, 'Times New Roman', serif", 2012, "websafe", {"sombre", "professional", "calm", "warm"}),
    ("Georgia, serif", "Verdana, Geneva, sans-serif", 2012, "websafe", None),
    ("'Trebuchet MS', Helvetica, sans-serif", "'Trebuchet MS', Helvetica, sans-serif", 2012, "websafe", {"community", "playful", "energetic", "warm"}),
    ("Arial, Helvetica, sans-serif", "Arial, Helvetica, sans-serif", 2012, "websafe", None),
    ("'Palatino Linotype', Palatino, serif", "Arial, sans-serif", 2012, "websafe", {"luxury", "sombre", "professional"}),
    ("Tahoma, Verdana, sans-serif", "Tahoma, Verdana, sans-serif", 2012, "websafe", {"technical", "community", "clinical"}),
    ("'Century Gothic', 'Apple Gothic', sans-serif", "Arial, sans-serif", 2012, "websafe", {"creative", "luxury", "playful"}),
    ("'Helvetica Neue', Helvetica, Arial, sans-serif", "'Helvetica Neue', Helvetica, Arial, sans-serif", 2012, "websafe", None),
    ("'Open Sans', Arial, sans-serif", "'Open Sans', Arial, sans-serif", 2012, "google", None),
    ("'Bitter', Georgia, serif", "'Open Sans', Arial, sans-serif", 2013, "google", {"warm", "earthy", "community", "professional"}),
    ("'Lato', sans-serif", "'Lato', sans-serif", 2012, "google", None),
    ("'Oswald', Impact, sans-serif", "'Open Sans', sans-serif", 2012, "google", {"energetic", "technical", "community"}),
    ("'Playfair Display', Georgia, serif", "'Lato', sans-serif", 2013, "google", {"luxury", "warm", "creative", "sombre"}),
    ("'Raleway', sans-serif", "'Open Sans', sans-serif", 2013, "google", {"luxury", "creative", "calm", "professional"}),
    ("'Merriweather', Georgia, serif", "'Merriweather Sans', sans-serif", 2013, "google", {"calm", "professional", "warm", "sombre"}),
    ("'Lobster', cursive", "'Droid Sans', sans-serif", 2012, "google", {"playful", "warm"}),
    ("'PT Serif', serif", "'PT Sans', sans-serif", 2012, "google", None),
    ("'Source Sans Pro', sans-serif", "'Source Sans Pro', sans-serif", 2012, "google", {"technical", "professional", "clinical", "calm"}),
    ("'Montserrat', sans-serif", "'Open Sans', sans-serif", 2014, "google", None),
    ("'Roboto Slab', serif", "'Roboto', sans-serif", 2014, "google", {"technical", "professional", "energetic", "community"}),
    ("'Libre Baskerville', serif", "'Source Sans Pro', sans-serif", 2014, "google", {"luxury", "calm", "sombre", "warm"}),
    ("'Amatic SC', cursive", "'Josefin Sans', sans-serif", 2014, "google", {"earthy", "playful", "warm"}),
    ("'Lora', serif", "'Lato', sans-serif", 2014, "google", {"warm", "calm", "earthy", "creative"}),
    ("'Josefin Sans', sans-serif", "'Lato', sans-serif", 2013, "google", {"creative", "luxury", "playful"}),
    ("'Rubik', sans-serif", "'Rubik', sans-serif", 2016, "google", {"playful", "technical", "energetic", "community"}),
    ("'Work Sans', sans-serif", "'Work Sans', sans-serif", 2016, "google", {"creative", "technical", "professional"}),
    ("system-ui stack (-apple-system, Segoe UI, Roboto…)", "system-ui stack", 2016, "system", {"technical", "professional", "clinical"}),
    ("'Poppins', sans-serif", "'Nunito Sans', sans-serif", 2017, "google", {"playful", "energetic", "technical", "community"}),
    ("'Cormorant Garamond', serif", "'Karla', sans-serif", 2017, "google", {"luxury", "calm", "creative", "earthy"}),
    ("'IBM Plex Sans', sans-serif", "'IBM Plex Serif', serif", 2018, "google", {"technical", "professional", "creative"}),
    ("'Space Mono', monospace", "'Work Sans', sans-serif", 2018, "google", {"creative", "technical", "energetic"}),
    ("'DM Serif Display', serif", "'DM Sans', sans-serif", 2019, "google", {"luxury", "warm", "creative", "calm"}),
    ("'Inter', sans-serif", "'Inter', sans-serif", 2020, "google", {"technical", "professional", "clinical", "calm"}),
    ("'Space Grotesk', sans-serif", "'Inter', sans-serif", 2020, "google", {"technical", "creative", "energetic"}),
    ("'Manrope', sans-serif", "'Manrope', sans-serif", 2020, "google", {"technical", "clinical", "calm", "professional"}),
    ("'Fraunces', serif", "'Commissioner', sans-serif", 2021, "google", {"warm", "earthy", "creative", "playful"}),
    ("'Recoleta' (self-hosted)", "'Karla', sans-serif", 2020, "selfhosted", {"warm", "playful", "earthy"}),
    ("'Syne', sans-serif", "'Inter', sans-serif", 2021, "google", {"creative", "energetic"}),
    ("'Outfit', sans-serif", "'Outfit', sans-serif", 2021, "google", {"playful", "technical", "energetic", "clinical"}),
    ("'Figtree', sans-serif", "'Figtree', sans-serif", 2022, "google", {"playful", "community", "clinical", "calm"}),
    ("'Instrument Serif', serif", "'Inter', sans-serif", 2023, "google", {"luxury", "creative", "calm", "professional"}),
    ("'Bricolage Grotesque', sans-serif", "'Bricolage Grotesque', sans-serif", 2023, "google", {"creative", "playful", "energetic"}),
    ("'Atkinson Hyperlegible', sans-serif", "'Atkinson Hyperlegible', sans-serif", 2020, "google", None),
    ("'Newsreader', serif", "'Newsreader', serif", 2021, "google", {"calm", "sombre", "creative", "professional"}),
    ("'Source Code Pro', monospace", "'Source Sans Pro', sans-serif", 2012, "google", {"technical", "clinical"}),
    ("'Fira Sans', sans-serif", "'Fira Sans', sans-serif", 2014, "google", {"technical", "professional", "community"}),
    ("'JetBrains Mono', monospace", "'Inter', sans-serif", 2020, "google", {"technical", "creative", "energetic"}),
    ("'Geist' (self-hosted)", "'Geist' (self-hosted)", 2023, "selfhosted", {"technical", "professional", "calm"}),
]

LATER_EDITS = [  # (first year, last year, change)
    (2012, 2026, "added a new page (whatever the business needed next)"),
    (2012, 2026, "plan tiers renamed; one old tier name left in a comment"),
    (2012, 2026, "new colour on buttons and links only"),
    (2012, 2026, "footer copyright year updated by hand"),
    (2012, 2026, "added a newsletter signup box"),
    (2012, 2026, "added a 'we're hiring' notice"),
    (2014, 2026, "added a status page link to the footer"),
    (2016, 2026, "added a 'book a demo' button to the header"),
    (2019, 2026, "replaced the hero illustration with a product screenshot drawn in SVG"),
    (2023, 2026, "added an 'AI-powered' badge to the hero"),
    (2023, 2026, "added a new AI feature section to the home page"),
    (2014, 2026, "mobile nav fix: hamburger menu bolted on"),
    (2014, 2019, "removed the Flash-era intro and the visitor counter"),
    (2015, 2026, "replaced the image slider with a static banner"),
    (2016, 2026, "added a free-trial link"),
    (2016, 2026, "removed the Facebook like box"),
    (2018, 2026, "added a cookie consent banner"),
    (2018, 2026, "added a privacy policy page"),
    (2019, 2020, "removed the Google+ button, added Instagram"),
    (2020, 2021, "added a 'we're remote-first now' notice"),
    (2019, 2026, "accessibility pass: focus styles, contrast tweaks, skip link"),
    (2020, 2026, "added a dark-mode toggle"),
    (2021, 2026, "swapped jQuery for a few lines of plain JS"),
    (2022, 2026, "new logo and heading font, rest left as it was"),
]

NAV_PATTERNS = [  # (first year, last year, text, min code skill)
    (2012, 2026, "top bar, logo left, links right", 1),
    (2012, 2026, "centred logo with links underneath", 1),
    (2012, 2016, "tabs across the top of a boxed layout", 1),
    (2012, 2018, "left sidebar navigation", 1),
    (2012, 2017, "horizontal bar with jQuery dropdowns", 2),
    (2014, 2026, "top bar that collapses to a hamburger on small screens", 2),
    (2016, 2026, "minimal: logo and a menu button that opens a full-screen overlay", 3),
    (2019, 2026, "sticky header that shrinks on scroll", 3),
    (2012, 2026, "links in the footer only, plus a small top strip", 1),
    (2012, 2026, "a single row of text links, separated by pipes", 1),
]

FOOTERS = ["four columns: product, company, resources, legal", "three-column info", "single line with copyright", "big footer with quick links and a short about blurb",
           "call-to-action strip and social icons", "dense sitemap footer", "small print plus credit line",
           "two columns: short about blurb and quick links"]

GRAPHIC_KINDS = {  # key: (text, first year)
    "css-gradient": ("CSS gradient decoration", 2012), "css-pattern": ("CSS pattern backgrounds", 2012),
    "css-shapes": ("CSS shapes and pseudo-element ornaments", 2012), "svg-illustration": ("SVG illustrations", 2013),
    "svg-icons": ("SVG icons", 2015), "icon-font": ("icon font glyphs", 2012), "svg-logo": ("an SVG logo", 2013),
    "text-wordmark": ("a styled text wordmark", 2012), "svg-pattern": ("SVG background patterns", 2015),
    "css-texture": ("CSS noise and texture effects", 2012),
    "svg-ui-mockup": ("product UI mockups drawn in SVG or CSS", 2013), "code-block": ("styled code samples as decoration", 2012),
    "svg-chart": ("SVG charts and diagrams", 2013),
}


# --- Structure habits (how this author tends to lay out a site's content) -----
# site.py's plan step follows these when it turns a theme into pages and sections.
ELEMENTS = ["long-text", "list", "table", "timeline", "gallery", "form", "code", "faq", "quote", "downloads"]

# Element weights by author (default 1). Favourite elements are drawn from these.
ELEMENT_LEAN = {
    "professional web designer": {"gallery": 2, "quote": 2, "timeline": 2},
    "graphic designer who moved to the web": {"gallery": 4, "quote": 3, "long-text": 2, "code": 0.2},
    "backend specialist doing the front end": {"table": 4, "form": 3, "list": 2, "gallery": 0.3},
    "junior developer or bootcamp graduate": {"list": 3, "faq": 2, "form": 2},
    "enterprise web team developer": {"downloads": 3, "faq": 3, "table": 2},
    "accessibility-focused developer": {"faq": 3, "long-text": 2, "list": 2},
    "founder-engineer": {"code": 3, "table": 2, "list": 2},
    "front-end perfectionist": {"long-text": 2, "timeline": 2, "table": 2},
    "marketing generalist": {"quote": 3, "list": 3, "form": 3, "code": 0.3},
    "in-house product designer": {"gallery": 3, "timeline": 2},
    "developer-relations engineer": {"code": 5, "timeline": 2, "table": 2},
}

# How many pages this author tends to build (site.py may move one step for the business).
PAGE_COUNTS = {"one page": 2, "2-3 pages": 4, "3-5 pages": 4, "5-8 pages": 2}

STRUCTURE_HABITS = [
    "long pages with a short list of in-page anchor links at the top",
    "many short pages rather than a few long ones",
    "the home page is mostly one long piece of prose with a few headings",
    "numbered sections on the longer pages",
    "a sidebar of quick facts that repeats on every page",
    "every page ends with the same one-line call to action",
    "puts the most useful table or list on the home page, near the top",
    "a short FAQ at the bottom of most pages",
    "pages open with a one-sentence summary in larger type",
    "a 'last updated' line at the bottom of each page",
    "groups content under plain questions as headings ('What does it do?', 'Who is it for?')",
    "a dense footer that doubles as the sitemap",
    "a news or updates strip on the home page, even if it's rarely updated",
    "side notes and asides set off in boxes",
    "one big comparison table that the rest of the site points to",
    "headings that are short labels, never sentences",
]

# =============================================================================
# SAMPLING HELPERS
# =============================================================================


def wpick(rng: random.Random, weights: dict):
    keys = [k for k, w in weights.items() if w > 0]
    return rng.choices(keys, weights=[weights[k] for k in keys])[0]


def parse_weights(spec: str) -> dict:
    return {k: float(v) for k, v in (p.split(":") for p in spec.split())}


def band_of(year: int) -> int:
    for i, (a, b) in enumerate(BANDS):
        if a <= year <= b:
            return i
    raise ValueError(year)


def overlap(a0, a1, b0, b1):
    lo, hi = max(a0, b0), min(a1, b1)
    return (lo, hi) if lo <= hi else None


def band_range(band: int, years):
    return overlap(*BANDS[band], *years)


def direction_ok(direction: str, yr, author: str) -> bool:
    y0, y1, dmin, dmax, *_ = DIRECTIONS[direction]
    if not overlap(y0, y1, *yr):
        return False
    amin, amax = AUTHORS[author]["design"]
    return bool(overlap(dmin, dmax, amin, amax))


CLASHES = {("neon on black", p) for p in ("sage & stone", "earthy browns", "burgundy & parchment")} | \
          {("AI-era aurora glow", p) for p in ("sage & stone", "earthy browns", "burgundy & parchment")}


def shared_moods(direction: str, palette: str) -> frozenset:
    """Moods a direction and a palette have in common: the manifest's possible taste."""
    if (direction, palette) in CLASHES:
        return frozenset()
    dmoods, pmoods = DIRECTIONS[direction][4], PALETTES[palette][3]
    return frozenset(pmoods if dmoods is None else dmoods & pmoods)


@lru_cache(maxsize=None)
def _dirs_for(yr, author):
    return tuple(d for d in DIRECTIONS if direction_ok(d, yr, author))


@lru_cache(maxsize=None)
def _pairs_for(direction):
    """(palette, tones) for every palette that suits the direction."""
    out = []
    for p in PALETTES:
        moods = shared_moods(direction, p)
        if moods:
            out.append((p, tuple(sorted({t for m in moods for t in TONE_FOR_MOOD[m]}))))
    return tuple(out)


def core_key(band, author, direction, palette, tone, copy_author) -> str:
    return "|".join([str(band), author, direction, palette, tone, copy_author])


def capacity(years=(YEAR_MIN, YEAR_MAX)) -> int:
    total = 0
    for band in range(len(BANDS)):
        yr = band_range(band, years)
        if not yr:
            continue
        for author in AUTHORS:
            for d in _dirs_for(yr, author):
                total += sum(len(tones) for _, tones in _pairs_for(d)) * len(COPY_AUTHORS)
    return total


# =============================================================================
# EXISTING MANIFESTS
# =============================================================================


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def signatures_of(m: dict):
    exact = hashlib.sha256(canonical(m).encode()).hexdigest()
    try:
        core = core_key(band_of(m["era"]["build_year"]), m["authorship"]["author"], m["visual"]["direction"],
                        m["visual"]["palette"]["family"], m["voice"]["tone"], m["voice"]["copy_author"])
    except (KeyError, TypeError, ValueError):
        core = None
    return exact, core


class Registry:
    """The output folder is the record. Tracks signatures of every manifest in it."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.seen_files: set[str] = set()
        self.exact: set[str] = set()
        self.core: set[str] = set()
        self._mtime = None
        self.refresh()

    def refresh(self, force=False):
        try:
            mtime = self.folder.stat().st_mtime_ns
        except FileNotFoundError:
            return
        if not force and mtime == self._mtime:
            return
        self._mtime = mtime
        with os.scandir(self.folder) as it:
            for e in it:
                if e.name in self.seen_files or e.name.startswith(".") or not e.name.endswith(".json"):
                    continue
                self.seen_files.add(e.name)
                try:
                    with open(e.path, encoding="utf-8") as f:
                        m = json.load(f)
                except (OSError, json.JSONDecodeError):
                    print(f"  warning: could not read {e.name}", file=sys.stderr)
                    continue
                exact, core = signatures_of(m)
                self.exact.add(exact)
                if core:
                    self.core.add(core)

    def __len__(self):
        return len(self.seen_files)


# =============================================================================
# SECTION MAKERS
# =============================================================================


def make_era(rng, yr, direction):
    dy = overlap(*yr, DIRECTIONS[direction][0], DIRECTIONS[direction][1])
    build = rng.randint(*dy)
    edits = []
    age = YEAR_MAX - build
    p = min(0.85, 0.15 + age * 0.08)
    if age > 0 and rng.random() < p:
        n_edits = 1 if age < 4 else rng.choice([1, 1, 2, 2, 3])
        yrs = sorted(rng.sample(range(build + 1, YEAR_MAX + 1), k=min(n_edits, age)))
        used = set()
        for y in yrs:
            options = [c for a, b, c in LATER_EDITS if a <= y <= b and c not in used]
            change = rng.choice(options)
            used.add(change)
            edits.append({"year": y, "change": change})
    tech = [f for yr, feats in TECH_TIMELINE if yr <= build for f in feats]
    not_yet = [f for yr, feats in TECH_TIMELINE if yr > build for f in feats]
    return {"build_year": build, "later_edits": edits, "tech_available": tech, "tech_not_yet": not_yet}


def make_authorship(rng, author, direction, year):
    a = AUTHORS[author]
    dmin, dmax = overlap(*a["design"], DIRECTIONS[direction][2], DIRECTIONS[direction][3])
    design = rng.randint(dmin, dmax)
    code = rng.randint(*a["code"])
    habits = {}
    for h, spec in [("naming", "naming"), ("units", "units"), ("reset", "reset"), ("media_queries", "media"),
                    ("files", "files"), ("markup", "markup"), ("js", "js"), ("icons", "icons")]:
        opts = HABITS[spec]
        w = {k: v for k, v in parse_weights(a[spec]).items() if opts[k][1] <= year <= opts[k][2]}
        if not w:
            w = {k: 1 for k, (_, y0, y1) in opts.items() if y0 <= year <= y1}
        habits[h] = opts[wpick(rng, w)][0]
    cw = parse_weights(a["comments"])
    c1 = wpick(rng, cw)
    comments = COMMENTS[c1]
    if rng.random() < 0.35:
        c2 = wpick(rng, {k: v for k, v in cw.items() if k != c1} or {c1: 1})
        if c2 != c1 and "none" not in (c1, c2):
            comments += "; also " + COMMENTS[c2]
    return {
        "author": author,
        "background": a["bg"],
        "design_skill": SKILL_WORDS[design],
        "code_skill": SKILL_WORDS[code],
        "habits": habits,
        "comments": comments,
        "imperfection": IMPERFECTION[wpick(rng, parse_weights(a["imperfection"]))],
    }, design, code


def hsl(h, s, l):
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360, max(0, min(1, l)), max(0, min(1, s)))
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def make_palette(rng, family, dark):
    (h0, h1), (s0, s1), acc, _moods, harmony = PALETTES[family]
    h = rng.uniform(h0, h1)
    s = rng.uniform(s0, s1)
    if acc:
        ah = rng.uniform(*acc)
    elif harmony == "triadic":
        ah = h + rng.choice([120, 240])
    else:
        ah = h + rng.choice([0, 180]) if harmony == "mono" else h + 30
    a_s = min(0.95, s + rng.uniform(-0.1, 0.2)) if s > 0.05 else rng.uniform(0, 0.8)
    mode = "dark" if dark else rng.choices(["light", "tinted", "dark"], weights=[6, 3, 1])[0]
    if mode == "light":
        bg, surface = hsl(h, s * 0.2, rng.uniform(0.96, 0.995)), "#ffffff"
        text, muted = hsl(h, s * 0.25, rng.uniform(0.12, 0.22)), hsl(h, s * 0.15, rng.uniform(0.42, 0.5))
        primary = hsl(h, s, rng.uniform(0.32, 0.45))
    elif mode == "tinted":
        bg, surface = hsl(h, s * 0.45, rng.uniform(0.9, 0.95)), hsl(h, s * 0.3, rng.uniform(0.97, 0.99))
        text, muted = hsl(h, s * 0.4, rng.uniform(0.12, 0.2)), hsl(h, s * 0.25, rng.uniform(0.38, 0.46))
        primary = hsl(h, s, rng.uniform(0.3, 0.42))
    else:
        bg, surface = hsl(h, s * 0.3, rng.uniform(0.06, 0.12)), hsl(h, s * 0.3, rng.uniform(0.13, 0.18))
        text, muted = hsl(h, s * 0.1, rng.uniform(0.86, 0.94)), hsl(h, s * 0.12, rng.uniform(0.6, 0.7))
        primary = hsl(h, s, rng.uniform(0.55, 0.68))
    accent = hsl(ah, a_s, rng.uniform(0.5, 0.62))
    border = hsl(h, s * 0.2, 0.85 if mode != "dark" else 0.25)
    return {"family": family, "mode": mode, "bg": bg, "surface": surface, "text": text,
            "primary": primary, "accent": accent, "muted": muted, "border": border}


def make_visual(rng, direction, palette_family, taste, year, author, design, code):
    y0, y1, _dmin, _dmax, _dm, dark, desc = DIRECTIONS[direction]
    # fonts
    fw = {}
    for i, (hd, bd, fy, kind, fmoods) in enumerate(FONT_PAIRS):
        if fy > year:
            continue
        w = 1.0
        if fmoods is not None:
            w = 2.0 if fmoods & set(taste) else 0.15
        if kind == "websafe":
            w *= 2.5 if year <= 2013 else (1.2 if year <= 2016 else 0.5)
            if design <= 2 or author == "enterprise web team developer":
                w *= 4
        elif kind in ("google", "selfhosted"):
            if design <= 2:
                w *= 0.4
            if kind == "google" and fy >= year - 2:
                w *= 1.6  # what was fashionable that year
        elif kind == "system":
            w *= 4 if author in ("backend specialist doing the front end", "founder-engineer", "developer-relations engineer") else 1
        if author == "accessibility-focused developer" and "Atkinson" in hd:
            w *= 4
        fw[i] = w
    hd, bd, *_ = FONT_PAIRS[wpick(rng, fw)]
    base = rng.choice(["12px", "13px", "13px", "14px"])
    if year > 2017:
        base = rng.choice(["16px", "16px", "17px", "18px", "1rem", "1.0625rem"])
    elif year > 2014:
        base = rng.choice(["14px", "15px", "16px", "16px"])
    scale = rng.choice([1.125, 1.2, 1.25, 1.333, 1.414, 1.5] if design >= 4 else [1.2, 1.25, 1.333, "inconsistent"])
    # layout
    if year <= 2014 and rng.random() < 0.75:
        width = f"fixed {rng.choice([940, 960, 960, 980, 1000, 1024])}px"
    elif year <= 2017:
        width = rng.choice([f"fixed {rng.choice([960, 1000, 1140, 1170])}px, fluid below it",
                            f"fluid, max-width {rng.choice([1000, 1100, 1140, 1200])}px"])
    else:
        width = rng.choice([f"fluid, max-width {rng.choice([1080, 1200, 1280, 1440])}px", "full-bleed bands, content max 70ch",
                            f"fluid, max-width {rng.choice([60, 64, 72, 80])}rem"])
    navs = [t for a, b, t, mc in NAV_PATTERNS if a <= year <= b and code >= mc]
    grids = ["single column", "two-column with sidebar", "two-column", "asymmetric 2:1", "centred narrow column"]
    if year <= 2016:
        grids += ["12-column float grid", "content plus right sidebar of boxes"]
    if year >= 2018:
        grids += ["CSS grid, varied row spans", "alternating left/right bands"]
    if year >= 2023:
        grids += ["bento-style tiles"]
    radius = {"soft pastel, rounded": ["12px", "16px", "24px"], "brutalist, raw": ["0"], "swiss grid, typographic": ["0"],
              "soft UI (neumorphism)": ["14px", "20px"], "glossy web 2.0 leftovers": ["8px", "10px"]}.get(
        direction, ["0", "2px", "3px", "4px", "6px", "8px"])
    shadow = {"material-inspired": "layered elevation shadows", "flat design, bold blocks": "none",
              "long-shadow flat": "long diagonal shadows on icons", "soft UI (neumorphism)": "paired light and dark shadows",
              "brutalist, raw": "hard offset shadows or none"}.get(
        direction, rng.choice(["none", "subtle", "soft drop shadows on boxes", "none"]))
    motion_opts = ["none", "hover colour changes only"]
    if year <= 2016:
        motion_opts += ["jQuery slide toggles and a fading slider", "parallax header"]
    if year >= 2015:
        motion_opts += ["CSS transitions on hover and menu"]
    if year >= 2019:
        motion_opts += ["fade-in on scroll (IntersectionObserver), respects reduced motion"]
    # graphics
    gw = {k: 1.0 for k, (_, gy) in GRAPHIC_KINDS.items() if gy <= year}
    if year < 2015:
        gw.update({k: gw[k] * 2.5 for k in ("css-gradient", "css-texture", "icon-font") if k in gw})
        if "svg-illustration" in gw:
            gw["svg-illustration"] *= 0.4
    else:
        gw.update({k: gw[k] * 2 for k in ("svg-icons", "svg-illustration", "svg-logo") if k in gw})
        gw["icon-font"] = gw.get("icon-font", 0) * (0.6 if year < 2020 else 0.2)
    if design <= 2:
        gw = {k: v * (2 if k in ("text-wordmark", "icon-font", "css-gradient") else 0.4) for k, v in gw.items()}
    if "illustration" in direction or "doodle" in direction:
        gw["svg-illustration"] = gw.get("svg-illustration", 0) + 8
    if direction in ("dev-tools dark, code-first", "docs-site style"):
        gw["code-block"] = gw.get("code-block", 0) + 6
    if direction in ("dashboard-as-marketing", "SaaS gradient landing page", "bento grid, product-style"):
        gw["svg-ui-mockup"] = gw.get("svg-ui-mockup", 0) + 6
        gw["svg-chart"] = gw.get("svg-chart", 0) + 3
    n_g = rng.choice([0, 1, 2, 2, 3, 3, 4]) if design > 1 else rng.choice([0, 0, 1, 2])
    graphics = []
    for _ in range(n_g):
        g = wpick(rng, {k: v for k, v in gw.items() if k not in graphics})
        graphics.append(g)
    if not graphics:
        graphics = ["none"]
    return {
        "direction": direction,
        "direction_notes": desc,
        "palette": make_palette(rng, palette_family, dark),
        "fonts": {"heading": hd, "body": bd},
        "type_scale": scale,
        "base_size": base,
        "layout": {"width": width, "nav": rng.choice(navs), "grid": rng.choice(grids), "footer": rng.choice(FOOTERS)},
        "shape": {"radius": rng.choice(radius), "shadow": shadow,
                  "borders": rng.choice(["thin rules", "none", "boxed panels", "dotted dividers", "thick top borders on sections"]),
                  "density": rng.choice(["tight", "comfortable", "comfortable", "airy"])},
        "motion": rng.choice(motion_opts),
        "graphics": graphics,
    }


# =============================================================================
# MANIFEST BUILDER
# =============================================================================


def _random_core(rng, years):
    bands = [b for b in range(len(BANDS)) if band_range(b, years)]
    band = rng.choice(bands)
    yr = band_range(band, years)
    author = wpick(rng, {a: AUTHORS[a]["era"][band] for a in AUTHORS})
    dirs = _dirs_for(yr, author)
    if not dirs:
        return None
    direction = rng.choice(dirs)
    pairs = _pairs_for(direction)
    if not pairs:
        return None
    palette, tones = rng.choice(pairs)
    return band, author, direction, palette, rng.choice(tones), wpick(rng, COPY_AUTHORS)


def pick_core(rng, reg: Registry, taken: set, years):
    """Choose an unused coherent (band, author, direction, palette, tone, copy author)."""
    for _ in range(4000):
        core = _random_core(rng, years)
        if core and (k := core_key(*core)) not in reg.core and k not in taken:
            return core
    # Near saturation: enumerate what is left.
    free = []
    for band in range(len(BANDS)):
        yr = band_range(band, years)
        if not yr:
            continue
        for author in AUTHORS:
            for d in _dirs_for(yr, author):
                for p, tones in _pairs_for(d):
                    for t in tones:
                        for c in COPY_AUTHORS:
                            k = core_key(band, author, d, p, t, c)
                            if k not in reg.core and k not in taken:
                                free.append((band, author, d, p, t, c))
    return rng.choice(free) if free else None


def make_voice(rng, tone, copy_author):
    return {
        "tone": tone,
        "personality": rng.choice(PERSONALITIES[tone]),
        "sample": rng.choice(VOICE_SAMPLES[tone]),
        "copy_author": copy_author,
    }


def make_taste(rng, direction, palette, tone):
    moods = sorted(m for m in shared_moods(direction, palette) if tone in TONE_FOR_MOOD[m])
    return sorted(rng.sample(moods, k=min(len(moods), rng.randint(1, 3))))


def make_structure(rng, author):
    lean = ELEMENT_LEAN.get(author, {})
    w = {e: lean.get(e, 1) for e in ELEMENTS}
    fav = []
    for _ in range(rng.choice([1, 2, 2, 3])):
        e = wpick(rng, {k: v for k, v in w.items() if k not in fav})
        fav.append(e)
    return {
        "density": rng.choice(["brochure", "brochure", "information-packed", "text-heavy"]),
        "pages": wpick(rng, PAGE_COUNTS),
        "favourite_elements": fav,
        "habit": rng.choice(STRUCTURE_HABITS),
    }


def build_manifest(rng, core, years):
    band, author, direction, palette, tone, copy_author = core
    era = make_era(rng, band_range(band, years), direction)
    year = era["build_year"]
    taste = make_taste(rng, direction, palette, tone)
    authorship, design, code = make_authorship(rng, author, direction, year)
    authorship["suits"] = [b for b in BUSINESS_TYPES if author not in BTYPE_EXCLUDES_AUTHORS.get(b, set())]
    visual = make_visual(rng, direction, palette, taste, year, author, design, code)
    return {
        "voice": make_voice(rng, tone, copy_author),
        "taste": taste,
        "structure": make_structure(rng, author),
        "era": era,
        "visual": visual,
        "authorship": authorship,
    }


# =============================================================================
# OUTPUT
# =============================================================================


def slugify(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:48] or "site"


def write_manifest(folder: Path, m: dict, exact: str) -> Path | None:
    words = re.split(r" (?:or|who|doing) ", m["authorship"]["author"])[0].split()[:3]
    base = f'{m["era"]["build_year"]}-{slugify(" ".join(words))}'
    data = json.dumps(m, indent=2, ensure_ascii=False) + "\n"
    for n in (4, 6, 8, 12):
        path = folder / f"{base}-{exact[:n]}.json"
        tmp = folder / f".{path.name}.{os.getpid()}.tmp"
        tmp.write_text(data, encoding="utf-8")
        try:
            os.link(tmp, path)  # atomic, fails if the name exists
        except FileExistsError:
            continue
        except OSError:  # filesystems without hard links
            if path.exists():
                continue
            os.replace(tmp, path)
            return path
        finally:
            if tmp.exists():
                tmp.unlink()
        return path
    return None


def parse_years(s):
    a, _, b = s.partition("-")
    a, b = int(a), int(b or a)
    if not (YEAR_MIN <= a <= b <= YEAR_MAX):
        raise argparse.ArgumentTypeError(f"years must be within {YEAR_MIN}-{YEAR_MAX}")
    return a, b


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write unique persona manifests for site.py.")
    ap.add_argument("--count", type=int, help="how many new manifests to write")
    ap.add_argument("--out", default="manifests", help="output folder (default ./manifests)")
    ap.add_argument("--years", type=parse_years, default=(YEAR_MIN, YEAR_MAX), help="build-year range, e.g. 2015-2020")
    ap.add_argument("--seed", type=int, help="random seed (for reproducing a run)")
    ap.add_argument("--capacity", action="store_true", help="report capacity and exit")
    args = ap.parse_args(argv)

    folder = Path(args.out)
    combos = capacity(args.years)
    total_cap = min(combos, CAP)
    if args.capacity or not args.count:
        reg = Registry(folder) if folder.exists() else None
        used = len(reg.core) if reg else 0
        print(f"capacity for {args.years[0]}–{args.years[1]}: {combos:,} unique core combinations, capped at {CAP:,}; "
              f"used in {folder}/: {used:,}; remaining: {max(0, total_cap - used):,}")
        if not args.count and not args.capacity:
            ap.print_usage()
        return 0
    if args.count < 1:
        ap.error("--count must be at least 1")

    folder.mkdir(parents=True, exist_ok=True)
    reg = Registry(folder)
    remaining = max(0, min(total_cap - len(reg.core), CAP - len(reg)))
    if args.count > remaining:
        print(f"refusing: asked for {args.count:,} but only {remaining:,} unique manifests remain "
              f"({len(reg):,} already in {folder}/).", file=sys.stderr)
        return 2

    rng = random.Random(args.seed if args.seed is not None else int.from_bytes(os.urandom(8), "big"))
    print(f"{len(reg):,} existing manifests in {folder}/; writing {args.count}...")
    taken: set[str] = set()
    written = 0
    while written < args.count:
        reg.refresh()
        core = pick_core(rng, reg, taken, args.years)
        if core is None:
            print("no unique combinations left.", file=sys.stderr)
            break
        m = build_manifest(rng, core, args.years)
        exact, ckey = signatures_of(m)
        reg.refresh()  # catch teammates writing at the same time
        if exact in reg.exact or ckey in reg.core:
            continue
        path = write_manifest(folder, m, exact)
        if not path:
            continue
        reg.seen_files.add(path.name)
        reg.exact.add(exact)
        reg.core.add(ckey)
        taken.add(ckey)
        written += 1
        print(f"  {path.name}  {m['era']['build_year']} · {m['authorship']['author']} · {m['visual']['direction']} · "
              f"{m['voice']['tone']} · {m['voice']['copy_author']}")
    left = max(0, total_cap - len(reg.core))
    print(f"done: {written} written; about {left:,} unique combinations left.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
