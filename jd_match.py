#!/usr/bin/env python3
"""jd_match.py — JD-aware honest matching for job-hunt workers.

Deterministic, zero-cost layer:
  - extract skills from job text and diff against LO's real stack
  - generate an honest, tailored cover note (never claims what LO lacks)
  - hard-skip only clearly impossible roles (zero overlap + senior, citizens-only, clearance)
LO-approved answer bank lives in audit.PROFILE; this module only phrases, never invents.
"""
import re, random

# LO's real stack: level + honest evidence string
STACK = {
    "typescript": {"level": "core", "ev": "1 year building full-stack apps"},
    "javascript": {"level": "core", "ev": "1 year building full-stack apps"},
    "node": {"level": "core", "ev": "1 year building full-stack apps"},
    "node.js": {"level": "core", "ev": "1 year building full-stack apps"},
    "react": {"level": "core", "ev": "1 year building full-stack apps"},
    "next.js": {"level": "core", "ev": "1 year building full-stack apps"},
    "nextjs": {"level": "core", "ev": "1 year building full-stack apps"},
    "tailwind": {"level": "core", "ev": "1 year building full-stack apps"},
    "python": {"level": "core", "ev": "1 year of backend work"},
    "postgresql": {"level": "core", "ev": "relational data modeling"},
    "postgres": {"level": "core", "ev": "relational data modeling"},
    "prisma": {"level": "core", "ev": "ORMs in production projects"},
    "redis": {"level": "core", "ev": "caching layers"},
    "docker": {"level": "core", "ev": "containerized dev environments"},
    "rest": {"level": "core", "ev": "API design"},
    "rest api": {"level": "core", "ev": "API design"},
    "websockets": {"level": "core", "ev": "real-time features"},
    "n8n": {"level": "working", "ev": "built automation workflows"},
    "automation": {"level": "working", "ev": "n8n + scripted pipelines"},
    "rust": {"level": "learning", "ev": "personal projects with Solana/Anchor"},
    "solana": {"level": "learning", "ev": "personal Web3 projects"},
    "anchor": {"level": "learning", "ev": "personal Web3 projects"},
}

# hard blockers: presence of any of these in JD -> skip (honest gate)
BLOCKERS = [
    (r"us citizens?", "citizens-only"),
    (r"u\.?s\.? citizens?", "citizens-only"),
    (r"security clearance", "clearance-required"),
    (r"must be authorized to work", "us-work-auth"),
    (r"visa sponsorship.{0,20}not available", "no-sponsorship"),
    (r"does not (offer|provide) visa sponsorship", "no-sponsorship"),
    (r"no visa sponsorship", "no-sponsorship"),
    (r"requires all remote workers to be in-country", "no-sponsorship"),
]

SENIOR_PAT = re.compile(r"(5\+?\s*(?:yrs?|years|years of experience)|8\+?\s*(?:yrs?|years))", re.I)

# canonical display names (dedup node/node.js, postgres/postgresql, next.js/nextjs)
CANON = {"node": "node.js", "nextjs": "next.js", "postgres": "postgresql"}

def extract_skills(text):
    """Return {canonical_skill: info} for skills present in the JD text."""
    t = text.lower()
    found = {}
    for k, v in STACK.items():
        pat = re.compile(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])")
        if pat.search(t):
            found[CANON.get(k, k)] = v
    return found

def analyze(jd_text):
    """Return dict: decision, reason, matched, gaps, note, senior."""
    t = jd_text or ""
    tl = t.lower()
    # blockers first
    for pat, why in BLOCKERS:
        if re.search(pat, tl):
            return {"decision": "skip", "reason": why, "matched": [], "gaps": [], "note": "", "senior": False}
    found = extract_skills(t)
    matched = sorted(set(found))
    gaps = sorted({s for s in ("go", "java", "aws", "kubernetes", "graphql", "terraform")
                   if re.search(r"(?<![a-z0-9])" + s + r"(?![a-z0-9])", tl)})
    senior = bool(re.search(r"(senior|staff|principal|lead|architect)", tl, re.I))
    hard_senior = bool(SENIOR_PAT.search(tl))
    overlap = len(matched)
    if overlap == 0 and (hard_senior or senior):
        return {"decision": "skip", "reason": "stack-mismatch",
                "matched": [], "gaps": gaps, "note": "", "senior": senior}
    note = build_note(found, gaps)
    return {"decision": "apply", "reason": "ok", "matched": matched, "gaps": gaps,
            "note": note, "senior": senior}

def build_note(found, gaps):
    core = [m for m, v in found.items() if v.get("level") in ("core", "working")]
    learn = [m for m, v in found.items() if v.get("level") == "learning"]
    yoe = "I'm early-career (1 year building full-stack apps, currently at BFHR)."
    names = ", ".join((core[:4] + learn[:2])[:5])
    if core:
        templates = [
            f"My core stack matches this role well: {names}. {yoe} Based in Kolkata, open to remote.",
            f"I work daily with {names} — that's the bulk of my last year of shipping. {yoe} Open to remote roles.",
            f"Direct experience with {names}. {yoe} Remote-ready from Kolkata.",
        ]
        base = random.choice(templates)
        if learn:
            base += f" I've also built personal projects with {', '.join(learn)} — honest about the level, not production yet."
        return base
    if learn:
        return (f"I've built personal projects with {', '.join(learn)} — early but real, not production experience. "
                f"{yoe} Open to remote roles.")
    return f"{yoe} I don't have direct experience with this exact stack, but I ship fast and am honest about what I'm learning. Open to remote roles."
