#!/usr/bin/env python3
"""Shared browser watchdog for job-hunt workers.

WHY THIS EXISTS
    When Chrome's CDP pipe wedges (renderer hang, driver bug, pipe death
    without EOF), Playwright's sync API blocks the main thread inside
    select() with no pending timers. A SIGALRM handler fires, but the
    raised exception cannot surface until the select returns — which never
    happens. Result: worker spins at 100% CPU forever, queue stalls, and
    systemd's Restart=always never kicks in because the process never dies.

    A watchdog THREAD is immune: it runs on its own thread, and after
    max_sec it SIGKILLs the browser process tree directly. The pipe hits
    EOF, Playwright raises "Target closed", and the job fails fast.

USAGE (per-job browser):
    wd = BrowserWatchdog(markers=["wf_w_wf-w1"], max_sec=240)
    wd.start()
    try:
        ... browser work ...
    finally:
        wd.stop()
    exit_if_fired(wd, log)   # True if watchdog killed the browser

    exit_if_fired() should exit the worker so systemd restarts it clean —
    a wedge can be environmental and every subsequent launch may hang too.
"""
import json
import os
import signal
import sqlite3
import threading
import time


def find_pids(markers):
    """PIDs of processes whose /proc cmdline contains any of `markers`."""
    if isinstance(markers, str):
        markers = [markers]
    needles = [m.encode() for m in markers]
    found = []
    me = str(os.getpid()).encode()
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        if entry.encode() == me:
            continue  # never kill ourselves
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                data = f.read()
        except (OSError, PermissionError, FileNotFoundError):
            continue
        if any(n in data for n in needles):
            found.append(int(entry))
    return found


class BrowserWatchdog:
    """SIGKILLs every process matching `markers` if not stopped in time."""

    def __init__(self, markers, max_sec=240, tick=5, grace=10, job=None):
        if isinstance(markers, str):
            markers = [markers]
        self.markers = markers
        self.max_sec = max_sec
        self.tick = tick
        self.grace = grace
        self.job = job  # optional (job_id, url) for poison-pill bookkeeping
        self.fired = threading.Event()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._stop.clear()
        self.fired.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"browser-watchdog-{self.markers[0][:20]}")
        self._thread.start()

    def stop(self):
        """Disarm the watchdog. MUST be called in a finally after browser
        work — a guard left armed on the success path will detonate mid-way
        through the NEXT job's browser."""
        self._stop.set()

    def _run(self):
        deadline = time.time() + self.max_sec
        while time.time() < deadline and not self._stop.wait(self.tick):
            pass
        if self._stop.is_set():
            return
        self.fired.set()
        pids = find_pids(self.markers)
        if pids:
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        if self.job:
            self._poison_check()
        # Killing the browser does NOT always unblock a wedged CDP call
        # (observed: main thread kept spinning in get_attribute after the
        # kill). Give the main thread a short grace period to unwind and
        # record the job as requeued, then SIGKILL ourselves so systemd
        # restarts the worker with a clean browser. The ExecStartPre
        # reset_claims step requeues any job still marked 'claimed'.
        grace_end = time.time() + self.grace
        while time.time() < grace_end:
            time.sleep(0.5)
        try:
            os.kill(os.getpid(), signal.SIGKILL)
        except Exception:
            pass

    def _poison_check(self):
        """Count wedge events per URL; after 3, permanently skip the job.

        A job whose page state reliably wedges the CDP pipe would otherwise
        bounce forever: claim -> wedge -> SIGKILL -> reset_claims requeues
        -> claim again. After 3 wedge events the job is marked skip with
        reason 'wedge-poison' so the queue moves on.
        """
        jid, url = self.job
        here = "/home/ubuntu/job_hunt_linkedin"
        wf = os.path.join(here, ".wedge_attempts.json")
        try:
            st = json.load(open(wf)) if os.path.exists(wf) else {}
        except Exception:
            st = {}
        st[url] = st.get(url, 0) + 1
        try:
            json.dump(st, open(wf, "w"))
        except Exception:
            pass
        if st[url] >= 3:
            try:
                conn = sqlite3.connect(os.path.join(here, "apply_queue.db"), timeout=5)
                conn.execute(
                    "UPDATE jobs SET status='skip', result='wedge-poison' WHERE id=? AND status='claimed'",
                    (jid,))
                conn.commit()
                conn.close()
            except Exception:
                pass


def exit_if_fired(wd, log):
    """If the watchdog killed the browser, exit so systemd restarts us.

    Returns True (and never returns False) so callers can `return` after.
    """
    if wd and wd.fired.is_set():
        try:
            log(f"BROWSER WATCHDOG FIRED — browser tree killed after {wd.max_sec}s, exiting for systemd restart")
        except Exception:
            pass
        os._exit(7)
    return False
