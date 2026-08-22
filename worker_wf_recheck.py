#!/usr/bin/env python3
"""Wellfound submit-unconfirmed rechecker — re-runs the fixed apply flow on jobs
that previously failed with submit-unconfirmed. The old worker was logged-out and
couldn't classify them; this worker benefits from login-recovery, external-apply
detection and location-block detection.
Usage: python3 worker_wf_recheck.py <worker_id>
"""
import json, os, re, sqlite3, sys, time, signal
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER_ID = sys.argv[1] if len(sys.argv) > 1 else "wf-r1"

# import the fixed apply flow from worker_wellfound
sys.argv = [sys.argv[0], WORKER_ID]
import worker_wellfound as ww

def log(msg):
    print(f"[{WORKER_ID}] [{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def db():
    return sqlite3.connect(os.path.join(HERE, "apply_queue.db"))

def claim_unconfirmed():
    """Claim one job that was skipped with submit-unconfirmed."""
    c = db()
    row = c.execute(
        "SELECT id, url, title FROM jobs WHERE portal='wellfound' AND status='skip' "
        "AND result LIKE 'submit-unconfirmed%' ORDER BY rowid LIMIT 1").fetchone()
    if not row:
        c.close(); ww.telemetry().idle(safe_detail="no-submit-unconfirmed"); return None
    upd = c.execute(
        "UPDATE jobs SET status='claimed', claimed_by=?, result='rechecking' "
        "WHERE id=? AND status='skip' AND result LIKE 'submit-unconfirmed%'",
        (WORKER_ID, row[0]))
    c.commit(); c.close()
    if upd.rowcount != 1:
        return claim_unconfirmed()
    ww.telemetry().claimed(row[0])
    return {"id": row[0], "url": row[1], "title": row[2]}

def main():
    def _alarm(signum, frame):
        raise TimeoutError("job hard timeout")
    signal.signal(signal.SIGALRM, _alarm)
    while True:
        job = claim_unconfirmed()
        if not job:
            log("no submit-unconfirmed jobs left — sleeping 10m")
            time.sleep(600); continue
        log(f"recheck: {job['title'][:50]}")
        signal.alarm(240)
        try:
            ok, reason = ww.apply_one(job["url"])
        except Exception as e:
            ok, reason = False, f"hard-timeout|{str(e)[:60]}"
        finally:
            signal.alarm(0)
        ww.mark(job["id"], "done" if ok else "skip", reason)
        log(f"{'DONE' if ok else 'SKIP'}: {reason[:90]}")
        time.sleep(3)

if __name__ == "__main__":
    main()
