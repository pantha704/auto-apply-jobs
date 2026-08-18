#!/usr/bin/env python3
"""Shared audit module for job-hunt workers.
- applications table: company, role, URL, timestamp, answers submitted, resume used, status
- per-application snapshots (screenshot + HTML before/after submit)
- aggressive dedup (unique canonical URL per portal)
"""
import json, os, re, sqlite3, time, hashlib
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "apply_queue.db")
AUDIT = os.path.join(HERE, "audits")

def canonical(url):
    if not url: return ""
    u = url.strip()
    u = re.sub(r"(\?|&)(trackingId|refId|trk|utm_[a-z]+|from|position|pageNum|gclid|fbclid|ref|lipi|currentJobId|originalSubdomain|shareId|searchId|secondarySharedUrl)[^&]*", "", u)
    u = re.sub(r"[?&]+$", "", u)
    return u.rstrip("/")

def db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        portal TEXT NOT NULL,
        company TEXT, role TEXT, url TEXT NOT NULL,
        applied_at TEXT NOT NULL,
        answers TEXT, resume_used TEXT,
        status TEXT, note TEXT,
        snap_before TEXT, snap_after TEXT,
        url_hash TEXT,
        UNIQUE(portal, url_hash)
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_apps_portal_time ON applications(portal, applied_at)")
    c.commit()
    return c

def record_application(portal, company, role, url, status, answers=None, resume_used=None,
                       note="", snap_before=None, snap_after=None):
    """Record an application attempt. Returns (True, id) if inserted, (False, None) if duplicate."""
    c = db()
    uh = hashlib.sha1(canonical(url).encode()).hexdigest()
    # aggressive dedup: same canonical URL = same role
    exists = c.execute("SELECT id FROM applications WHERE portal=? AND url_hash=?", (portal, uh)).fetchone()
    if exists:
        c.close()
        return (False, exists[0])
    now = datetime.now(timezone.utc).isoformat()
    try:
        cur = c.execute("""INSERT INTO applications
        (portal, company, role, url, applied_at, answers, resume_used, status, note, snap_before, snap_after, url_hash)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (portal, company, role, url, now,
         json.dumps(answers, ensure_ascii=False) if answers else None,
         resume_used, status, note, snap_before, snap_after, uh))
        c.commit()
        rid = cur.lastrowid
    except sqlite3.IntegrityError:
        c.rollback()
        c.close()
        return (False, None)
    c.close()
    return (True, rid)

def snapshot(page, portal, job_id, stage):
    """Save screenshot + HTML for audit. Returns paths dict."""
    d = os.path.join(AUDIT, portal, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    os.makedirs(d, exist_ok=True)
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", (job_id or "job")[:40])
    ts = int(time.time())
    base = os.path.join(d, f"{safe}_{ts}_{stage}")
    try:
        page.screenshot(path=base + ".png", full_page=False)
    except Exception:
        base_png = None
    else:
        base_png = base + ".png"
    try:
        html = page.content()
        open(base + ".html", "w").write(html)
    except Exception:
        html_path = None
    else:
        html_path = base + ".html"
    return {"png": base_png, "html": html_path}

# Honest answer bank (never invent beyond this). Identity from profile_local / env.
import profile as ident
PROFILE = {
    "name": ident.NAME,
    "email": ident.EMAIL,
    "phone": ident.PHONE,
    "address": ident.ADDRESS,
    "city": ident.CITY, "state": ident.STATE, "pin": ident.PIN,
    "linkedin": "https://www.linkedin.com/in/pantha704",
    "portfolio": "https://pantha704.github.io",
    "college": ident.COLLEGE,
    "expected_lpa": "700000", "current_lpa": "480000", "notice": "0",
    "years": "1",
    "work_auth_us": "No",
    "sponsorship": "Yes",  # requires visa sponsorship — honest Yes on sponsor questions
    "relocate": "No",
    "education_completed": "No",
    "excel_years": "1",
    "note": "Full-stack engineer (TypeScript/Python/Rust) with 1 year of experience, based in Kolkata, open to remote roles.",
    "stack": "TypeScript, JavaScript, Python, Rust, Node.js, Next.js, React, Tailwind CSS, PostgreSQL, Prisma, Redis, Docker, Solana/Anchor, REST APIs, WebSockets",
    "resume": os.environ.get("JOBHUNT_RESUME", os.path.join(HERE, "resume.pdf")),
}
