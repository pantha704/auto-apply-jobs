#!/usr/bin/env python3
"""Reviewer worker — the careful second pass for ambiguous job results.

Fast workers classify confusing formats and move on (honest skip with a
machine-readable reason). This worker picks up ONLY that ambiguous bucket and
re-examines each job with more patience: up to 2 attempts per job, hard
timeout, portal-specific re-run of the real apply flow.

Buckets reviewed (per portal):
  - wellfound:   submit-unconfirmed, no-apply-modal, send-unconfirmed, fill-err
  - yc:          send-unconfirmed, no-apply-modal, no-send-button, fill-err,
                 no-apply-button, error|*
  - internshala: (future — form flow is fixed-shape, rarely ambiguous)
  - external:    unhandled-source (re-routes through worker_external)

Usage: python3 worker_review.py <worker_id>
"""
import json, os, re, sqlite3, sys, time, signal
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER_ID = sys.argv[1] if len(sys.argv) > 1 else "rev-w1"

sys.argv = [sys.argv[0], WORKER_ID]
import worker_wellfound as ww
import worker_yc as wy
import worker_external as wex
from worker_guard import BrowserWatchdog
from workflow.worker_telemetry import telemetry_for

AMBIGUOUS = ("submit-unconfirmed", "no-apply-modal", "send-unconfirmed",
             "no-send-button", "fill-err", "unhandled-source")
QUEUE_DB = os.getenv("JOBHUNT_QUEUE_DB", os.path.join(HERE, "apply_queue.db"))
STATE_ROOT = os.getenv("JOBHUNT_STATE_ROOT", os.path.join(HERE, "state_queue"))


def telemetry():
    return telemetry_for(WORKER_ID, "review", QUEUE_DB, STATE_ROOT)


def log(msg):
    print(f"[{WORKER_ID}] [{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def db():
    return sqlite3.connect(QUEUE_DB)


def claim_ambiguous():
    """Claim one ambiguous job across portals (skip-bucket, oldest first)."""
    c = db()
    like = " OR ".join(["result LIKE ?"] * len(AMBIGUOUS))
    row = c.execute(
        f"SELECT id, url, title, portal, result FROM jobs "
        f"WHERE status='skip' AND ({like}) AND result NOT LIKE 'reviewed%' "
        f"ORDER BY rowid LIMIT 1",
        [f"{a}%" for a in AMBIGUOUS]).fetchone()
    if not row:
        c.close()
        telemetry().idle(safe_detail="no-ambiguous-jobs")
        return None
    upd = c.execute(
        f"UPDATE jobs SET status='claimed', claimed_by=?, result='reviewing' "
        f"WHERE id=? AND status='skip' AND ({like}) AND result NOT LIKE 'reviewed%'",
        [WORKER_ID, row[0]] + [f"{a}%" for a in AMBIGUOUS])
    c.commit()
    c.close()
    if upd.rowcount != 1:
        return claim_ambiguous()
    telemetry().claimed(row[0])
    return {"id": row[0], "url": row[1], "title": row[2], "portal": row[3], "prev": row[4]}


def mark(jid, status, result):
    c = db()
    c.execute("UPDATE jobs SET status=?, result=? WHERE id=?", (status, result[:250], jid))
    c.commit()
    c.close()
    telemetry().outcome(jid, status, result)


def review(job):
    portal = job["portal"]
    url = job["url"]
    attempts = 0
    while attempts < 2:
        attempts += 1
        try:
            if portal == "wellfound":
                ok, reason = ww.apply_one(url)
                if ok:
                    return mark(job["id"], "done", f"reviewed|{reason}")
                # failed again — only retry on retryable-looking reasons
                if "timeout" in reason.lower() or "error|" in reason:
                    time.sleep(5)
                    continue
                return mark(job["id"], "skip", f"reviewed|{reason}")
            if portal == "yc":
                status, reason, note = wy.apply_url(url)
                if status == "submitted":
                    audit_record(job, url, note)
                    return mark(job["id"], "done", "reviewed|submitted")
                if reason.startswith("error|") or "timeout" in reason.lower():
                    time.sleep(5)
                    continue
                return mark(job["id"], "skip", f"reviewed|{reason}")
            if portal == "external":
                src = "weworkremotely" if "weworkremotely" in url else \
                      "himalayas" if "himalayas" in url else "yc" if "workatastartup" in url else \
                      "naukri" if "naukri" in url else "unknown"
                ok, reason = wex.route({"url": url, "source": src})
                if ok:
                    return mark(job["id"], "done", f"reviewed|{reason}")
                return mark(job["id"], "skip", f"reviewed|{reason}")
            return mark(job["id"], "skip", f"reviewed|no-handler:{portal}")
        except Exception as e:
            reason = f"error|{str(e)[:100]}"
            if attempts == 2:
                return mark(job["id"], "skip", f"reviewed|{reason}")
            time.sleep(5)
    mark(job["id"], "skip", "reviewed|gave-up")


def audit_record(job, url, note):
    import audit
    audit.record_application("yc", job["title"], job["title"], url,
                             "submitted", answers={"note": (note or "")[:400]},
                             resume_used="/home/ubuntu/Documents/Pratham_Jaiswal_Updated_Resume.pdf",
                             note="reviewed")


def main():
    def _alarm(signum, frame):
        raise TimeoutError("job hard timeout")
    signal.signal(signal.SIGALRM, _alarm)
    log("reviewer started")
    while True:
        job = claim_ambiguous()
        if not job:
            log("no ambiguous jobs — sleeping 5m")
            time.sleep(300)
            continue
        log(f"review [{job['portal']}] {job['title'][:50]} (prev={job['prev']})")
        signal.alarm(240)
        GUARD = BrowserWatchdog([f"ext_hima_{WORKER_ID}", f"ext_wwr_{WORKER_ID}"], max_sec=270,
                                job=(job["id"], job["url"]))
        GUARD.start()
        try:
            review(job)
        except TimeoutError:
            mark(job["id"], "skip", "reviewed|hard-timeout")
        finally:
            signal.alarm(0)
            GUARD.stop()
        if GUARD.fired.is_set():
            mark(job["id"], "pending", "reviewed|browser-wedge-timeout")
            log("BROWSER WEDGE — job requeued, exiting for systemd restart")
            os._exit(7)
        log(f"-> {job['id']} done")
        time.sleep(4)


if __name__ == "__main__":
    main()
