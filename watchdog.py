#!/usr/bin/env python3
"""Job-hunt watchdog: alerts AND self-heals wedged workers.

Exit 0 + no output = healthy (silent). Exit 1 + message = alert needed.

Self-healing (auto systemctl restart) for:
  1. CPU spin       — worker process pegged >90% CPU across 2 samples
                      (the classic wedged-CDP-pipe failure mode)
  2. Claim-stuck    — worker logged "claim:" but no DONE/SKIP for 10+ min
                      (idle-wait wedge: process alive, event loop blocked)
  3. No such worker at all — systemd should restart it; only alert here.

Restarts are portal-scoped (restart all instances of that portal's unit
template) and cooldown-limited (20 min per portal) so a broken portal
cannot restart-loop itself into oblivion.
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

HERE = "/home/ubuntu/job_hunt_linkedin"
DB = os.path.join(HERE, "apply_queue.db")
STATE_FILE = os.path.join(HERE, ".watchdog_state.json")
RESTART_FILE = os.path.join(HERE, ".watchdog_restarts.json")
SPIN_FILE = os.path.join(HERE, ".watchdog_spin.json")

now = datetime.now(timezone.utc)
ALERTS = []
COOLDOWN_MIN = 360  # each alert category fires at most once per 6 hours
RESTART_COOLDOWN = 1200  # per-portal auto-restart cooldown (20 min)
SPIN_WINDOW = 900  # spin samples considered "consecutive" within 15 min

# portal -> systemd unit template
PORTAL_UNITS = {
    "wellfound": "jobhunt-wf@*",
    "linkedin": "jobhunt-li@*",
    "internshala": "jobhunt-is@*",
    "external": "jobhunt-ext@*",
    "yc": "jobhunt-yc@*",
    "review": "jobhunt-review@*",
}


def alert(key, msg):
    """Append an alert only if this category hasn't fired within the cooldown."""
    try:
        st = json.load(open(STATE_FILE))
    except Exception:
        st = {}
    last = st.get(key, 0)
    if time.time() - last >= COOLDOWN_MIN * 60:
        ALERTS.append(msg)
        st[key] = time.time()
        try:
            json.dump(st, open(STATE_FILE, "w"))
        except Exception:
            pass


def restart_portal(portal, reason):
    """systemctl restart all units of a portal, cooldown-limited."""
    unit_pat = PORTAL_UNITS.get(portal)
    if not unit_pat:
        return False
    try:
        st = json.load(open(RESTART_FILE))
    except Exception:
        st = {}
    if time.time() - st.get(portal, 0) < RESTART_COOLDOWN:
        return False
    st[portal] = time.time()
    try:
        json.dump(st, open(RESTART_FILE, "w"))
    except Exception:
        pass
    r = subprocess.run(
        f"systemctl list-units '{unit_pat}' --no-legend --no-pager -o name",
        shell=True, capture_output=True, text=True, timeout=30)
    units = [u.strip() for u in r.stdout.splitlines() if u.strip()]
    if not units:
        ALERTS.append(f"no units matched {unit_pat} for restart ({reason})")
        return False
    rr = subprocess.run(
        f"sudo systemctl restart {' '.join(units)}",
        shell=True, capture_output=True, text=True, timeout=90)
    msg = f"AUTO-RESTARTED {', '.join(units)} — {reason}"
    if rr.returncode != 0:
        msg += f" (restart rc={rr.returncode}: {rr.stderr.strip()[:100]})"
    ALERTS.append(msg)
    return True


def get_workers():
    """List of {pid, pcpu, etimes, cmd, portal} for worker python procs."""
    out = subprocess.run(
        "ps -eo pid,pcpu,etimes,cmd | grep -E '[w]orker_(linkedin|wellfound|internshala|external|yc|review)\\.py'",
        shell=True, capture_output=True, text=True, timeout=30).stdout
    workers = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        pid, pcpu, etimes, cmd = parts
        m = re.search(r"worker_(\w+)\.py", cmd)
        if not m:
            continue
        portal = m.group(1)
        if portal == "review":
            portal = "review"
        try:
            workers.append({"pid": pid, "pcpu": float(pcpu),
                            "etimes": int(etimes), "cmd": cmd, "portal": portal})
        except ValueError:
            continue
    return workers


# ---- 1. worker processes alive? ----
workers = get_workers()
if not workers:
    ALERTS.append("NO WORKER PROCESSES RUNNING (systemd units dead?)")

# ---- 1b. CPU-spin detection (multi-sample) ----
try:
    st = json.load(open(SPIN_FILE))
except Exception:
    st = {}
now_ts = time.time()
for w in workers:
    pid = w["pid"]
    samples = st.get(pid, [])
    if w["pcpu"] > 90:
        samples.append(now_ts)
    samples = [t for t in samples if now_ts - t <= SPIN_WINDOW][-4:]
    st[pid] = samples
    if len(samples) >= 2:
        spin_min = (samples[-1] - samples[0]) / 60
        if restart_portal(w["portal"], f"{w['cmd'].split()[-1]} pegged {w['pcpu']:.0f}% CPU across samples"):
            st.pop(pid, None)
for pid in list(st.keys()):
    if pid not in {w["pid"] for w in workers}:
        st.pop(pid, None)
json.dump(st, open(SPIN_FILE, "w"))

# ---- 1c. claim-stuck detection (journal-based) ----
try:
    r = subprocess.run(
        "systemctl list-units 'jobhunt-*' --no-legend --no-pager -o name --state=active",
        shell=True, capture_output=True, text=True, timeout=30)
    active_units = [u.strip() for u in r.stdout.splitlines() if u.strip()]
    for unit in active_units:
        jr = subprocess.run(
            f"sudo journalctl -u {unit} --since '25 minutes ago' --no-pager -o short-iso",
            shell=True, capture_output=True, text=True, timeout=30)
        lines = jr.stdout.splitlines()
        claims = [l for l in lines if "claim:" in l]
        if not claims:
            continue
        # last claim timestamp; if the final line of the window is still a
        # claim (no DONE/SKIP after), the worker is stuck on that job
        outcomes = [l for l in lines if "DONE" in l or "SKIP" in l or "ERROR" in l
                    or "BROWSER WEDGE" in l or "queue empty" in l]
        last_claim = claims[-1]
        ts_m = re.match(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})", last_claim)
        last_out = outcomes[-1] if outcomes else ""
        ts_o = re.match(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})", last_out) if last_out else None
        if not ts_m:
            continue
        claim_t = datetime.fromisoformat(ts_m.group(1).replace(" ", "T"))
        age_min = (now - claim_t.replace(tzinfo=timezone.utc)).total_seconds() / 60
        if age_min < 10:
            continue
        if ts_o and ts_o.group(1) >= ts_m.group(1):
            continue  # outcome after claim — worker moved on
        portal = None
        for p, pat in PORTAL_UNITS.items():
            if pat.split("@")[0] in unit:
                portal = p
                break
        if portal:
            restart_portal(portal, f"{unit} claim-stuck {age_min:.0f} min (last claim no outcome)")
except Exception as e:
    ALERTS.append(f"claim-stuck check err: {e}")

# ---- 2. queue health ----
c = sqlite3.connect(DB)
pending = c.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
done_total = c.execute("SELECT COUNT(*) FROM jobs WHERE status='done'").fetchone()[0]
wf_done = c.execute("SELECT COUNT(*) FROM jobs WHERE portal='wellfound' AND status='done'").fetchone()[0]
li_done = c.execute("SELECT COUNT(*) FROM jobs WHERE portal='linkedin' AND status='done'").fetchone()[0]
li_pending = c.execute("SELECT COUNT(*) FROM jobs WHERE portal='linkedin' AND status='pending'").fetchone()[0]
c.close()
if pending == 0:
    ALERTS.append(f"QUEUE EXHAUSTED: 0 pending (done={done_total}) — needs fresh scrape")

# ---- 3. stalled worker detection — DB activity is the source of truth ----
try:
    c = sqlite3.connect(DB)
    latest = c.execute("SELECT MAX(applied_at) FROM applications").fetchone()[0]
    c.close()
    if latest:
        latest_dt = datetime.fromisoformat(latest)
        age_min = (now - latest_dt).total_seconds() / 60
        if age_min > 45:
            alert("db_activity", f"no application activity in DB for {age_min:.0f} min")
    else:
        alert("db_activity", "applications table empty — no audit rows at all")
except Exception as e:
    ALERTS.append(f"db activity check err: {e}")

# ---- 3b. submissions, not just activity ----
try:
    c = sqlite3.connect(DB)
    last_sub = c.execute("SELECT MAX(applied_at) FROM applications WHERE status='submitted'").fetchone()[0]
    wf_pending_n = c.execute("SELECT COUNT(*) FROM jobs WHERE portal='wellfound' AND status='pending'").fetchone()[0]
    c.close()
    if wf_pending_n > 20:
        if last_sub:
            sub_age = (now - datetime.fromisoformat(last_sub)).total_seconds() / 60
            if sub_age > 90:
                alert("no_submission", f"no successful submission in {sub_age:.0f} min with {wf_pending_n} wellfound pending — workers may be skipping junk")
        else:
            alert("no_submission", f"zero successful submissions ever, {wf_pending_n} wellfound pending")
except Exception as e:
    ALERTS.append(f"submission check err: {e}")

# ---- 4. error flood from live journal (replaces stale log files) ----
try:
    jr = subprocess.run(
        "sudo journalctl -u 'jobhunt-*' --since '15 minutes ago' --no-pager | grep -cE 'ERROR|no-submit-btn' || true",
        shell=True, capture_output=True, text=True, timeout=60)
    errs = int(jr.stdout.strip() or 0)
    jr2 = subprocess.run(
        "sudo journalctl -u 'jobhunt-*' --since '15 minutes ago' --no-pager | wc -l",
        shell=True, capture_output=True, text=True, timeout=60)
    total = int(jr2.stdout.strip() or 0)
    if total > 50 and errs / max(total, 1) > 0.6:
        alert("err_flood", f"error flood: {errs}/{total} recent worker lines are errors")
except Exception as e:
    ALERTS.append(f"err flood check err: {e}")

# ---- 5. LinkedIn session health ----
try:
    li = json.load(open(os.path.join(HERE, "li_state.json")))
    has_li_at = any(c.get("name") == "li_at" for c in li.get("cookies", []))
    if not has_li_at and li_pending > 1000:
        alert("li_at", "LINKEDIN SESSION MISSING li_at — li workers cannot apply (relogin needed; retry scheduled)")
except Exception as e:
    ALERTS.append(f"li_state err: {e}")

# ---- 6. scraper cron health ----
try:
    for fname, max_age_h, label in [
        ("jobs_raw_r3600_india.json", 3.0, "fresh-1h (hourly LinkedIn pass)"),
        ("jobs_raw_r86400_india.json", 7.0, "fresh-24h (6h LinkedIn pass)"),
        ("/tmp/site_collect.json", 5.0, "refill (3h collect+inject)"),
    ]:
        p = fname if fname.startswith("/") else os.path.join(HERE, fname)
        if not os.path.exists(p):
            alert("cron_stale", f"{label}: checkpoint {fname} MISSING — cron never ran?")
            continue
        age_h = (time.time() - os.path.getmtime(p)) / 3600
        if age_h > max_age_h:
            alert("cron_stale", f"{label}: checkpoint {age_h:.1f}h old — scrape cron likely FAILING")
except Exception as e:
    ALERTS.append(f"cron health err: {e}")

# ---- 7. submissions-rate floor ----
try:
    c = sqlite3.connect(DB)
    subs_2h = c.execute(
        "SELECT COUNT(*) FROM applications WHERE status IN ('submitted','applied') AND applied_at > ?",
        ((now - timedelta(hours=2)).isoformat(),)).fetchone()[0]
    pend = c.execute("SELECT COUNT(*) FROM jobs WHERE status='pending' AND portal IN ('wellfound','yc','internshala')").fetchone()[0]
    c.close()
    if subs_2h == 0 and pend > 10:
        alert("rate_floor", f"ZERO submissions in 2h with {pend} pending — workers alive but flow broken (detector/fill regression?)")
except Exception as e:
    ALERTS.append(f"rate floor err: {e}")

# ---- 8. worker restart storm (fast) — exit-on-empty crash-thrash (pitfall 45) ----
# A worker whose empty-branch EXITS under Restart=always climbs NRestarts every few
# seconds forever (YC hit 782). rate_floor only catches it after its 6h cooldown;
# this trips immediately. Compare NRestarts against a per-worker baseline we store.
try:
    state_p = os.path.join(HERE, ".watchdog_restarts.json")
    prev = {}
    try:
        prev = json.load(open(state_p))
    except Exception:
        prev = {}
    cur = {}
    procs = subprocess.run("systemctl list-units 'jobhunt-*' --no-pager --no-legend",
                           shell=True, capture_output=True, text=True, timeout=30)
    units = [l.split()[0] for l in procs.stdout.splitlines() if "jobhunt-" in l]
    for u in units:
        if not u.endswith(".service"):
            continue
        r = subprocess.run(f"systemctl show {u} -p NRestarts --no-pager",
                           shell=True, capture_output=True, text=True, timeout=20)
        try:
            nr = int(r.stdout.strip().split("=")[-1])
        except Exception:
            continue
        cur[u] = nr
    for u, nr in cur.items():
        p = prev.get(u, 0)
        if nr >= 20 and nr - p >= 15:   # restarting fast since last check (thrash)
            alert("restart_storm", f"{u}: {nr} restarts (delta {nr-p})+ — likely exit-on-empty or wedge loop; 'applications.applied_at' stalled? VERIFY: journalctl -u {u}")
    json.dump(cur, open(state_p, "w"))
except Exception as e:
    ALERTS.append(f"restart-storm check err: {e}")

if ALERTS:
    print("WATCHDOG ALERTS:")
    for a in ALERTS:
        print(" -", a)
    print(f"\nstate: pending={pending} done={done_total} (wf={wf_done}, li={li_done})")
    sys.exit(1)
sys.exit(0)
