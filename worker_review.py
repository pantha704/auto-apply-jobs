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
from workflow.application_gate import (
    PublicationUnavailable,
    eligible_for_claim,
    published_runtime,
)

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
    """Gate, pin, and claim one ambiguous job across portals."""
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
    portal, url = row[3], row[1] or ""
    if portal == "wellfound":
        runtime = published_runtime("wellfound", ww.REQUIRED_FACTS)
        ww.configure_publication(runtime)
    elif portal == "yc":
        runtime = published_runtime("yc", wy.REQUIRED_FACTS)
        wy.configure_publication(runtime)
    elif portal == "external":
        needs_session = "himalayas" in url
        runtime = published_runtime("himalayas" if needs_session else None, wex.REQUIRED_FACTS, require_session=needs_session)
        wex.configure_publication(runtime)
    else:
        c.close()
        raise PublicationUnavailable("review portal has no published runtime")
    if not eligible_for_claim(runtime, {"title": row[2] or "", "portal": portal}):
        c.execute("UPDATE jobs SET result='reviewed|policy-ineligible' WHERE id=? AND status='skip'", (row[0],))
        c.commit(); c.close()
        telemetry().outcome(row[0], "skip", "policy-ineligible")
        return claim_ambiguous()
    upd = c.execute(
        f"""UPDATE jobs SET status='claimed',claimed_by=?,result='reviewing',
            candidate_profile_id=?,candidate_profile_revision=?,resume_version_id=?,
            preference_set_id=?,preference_set_version=?,portal_session_revision=?
            WHERE id=? AND status='skip' AND ({like}) AND result NOT LIKE 'reviewed%'""",
        [WORKER_ID, runtime.profile_id, runtime.profile_revision, runtime.resume_id,
         runtime.preference_set.id, runtime.preference_set.version, runtime.session_revision,
         row[0]] + [f"{a}%" for a in AMBIGUOUS])
    c.commit(); c.close()
    if upd.rowcount != 1:
        return claim_ambiguous()
    telemetry().claimed(row[0])
    return {"id": row[0], "url": row[1], "title": row[2], "portal": row[3],
            "prev": row[4], "runtime": runtime}


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
                ok, reason = ww.apply_one(url, expected_session_revision=job["runtime"].session_revision)
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
                ok, reason = wex.route({"url": url, "source": src}, job["runtime"].session_revision or None)
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
                             resume_used=job["runtime"].resume_path,
                             note="reviewed")


def main():
    def _alarm(signum, frame):
        raise TimeoutError("job hard timeout")
    signal.signal(signal.SIGALRM, _alarm)
    log("reviewer started")
    while True:
        try:
            job = claim_ambiguous()
        except PublicationUnavailable:
            log("published profile/policy/session not ready — waiting before claim")
            time.sleep(300)
            continue
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
