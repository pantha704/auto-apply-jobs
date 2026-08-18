#!/usr/bin/env python3
"""Re-audit wellfound 'submitted' rows: detect location-blocked phantoms via after-HTML snapshots."""
import sqlite3, os, sys
sys.path.insert(0, '/home/ubuntu/job_hunt_linkedin')
import audit

con = sqlite3.connect('/home/ubuntu/job_hunt_linkedin/apply_queue.db')
cur = con.cursor()

BLOCK_MARKERS = ["not accepting applications", "timezone or relocation"]

rows = cur.execute(
    "SELECT id, url, snap_after FROM applications WHERE portal='wellfound' AND status='submitted'"
).fetchall()

phantoms = []
unreadable = 0
for aid, url, snap_after in rows:
    if not snap_after:
        unreadable += 1
        continue
    html = snap_after[:-4] + '.html'  # sibling html of the after png
    if not os.path.exists(html):
        unreadable += 1
        continue
    try:
        content = open(html, encoding='utf-8', errors='ignore').read().lower()
    except Exception:
        unreadable += 1
        continue
    if any(m in content for m in BLOCK_MARKERS):
        phantoms.append((aid, url))

print(f'total submitted rows checked: {len(rows)}')
print(f'phantom (location-blocked) rows: {len(phantoms)}')
print(f'rows with no readable snapshot: {unreadable}')

if phantoms:
    for aid, url in phantoms:
        cur.execute(
            "UPDATE applications SET status='skipped:location-block', note='re-audit: Wellfound location/timezone block, submit never happened' WHERE id=?",
            (aid,),
        )
    # python-side job update with canonical matching
    for aid, url in phantoms:
        canon = audit.canonical(url)
        for (jid, jurl) in cur.execute("SELECT id, url FROM jobs WHERE portal='wellfound' AND status='done'").fetchall():
            if audit.canonical(jurl) == canon:
                cur.execute("UPDATE jobs SET status='skip' WHERE id=?", (jid,))
    con.commit()
    print('reclassified applications rows + matching jobs rows to skip')

print('--- post-fix breakdown ---')
for r in cur.execute("SELECT status, count(*) FROM applications WHERE portal='wellfound' GROUP BY 1"):
    print(r)
con.close()
