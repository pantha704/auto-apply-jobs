# Architecture — what exists and how it is supposed to work

This is the map of the farm. Agents: treat this as ground truth. If code and this
file disagree, **read the code**, then update this file in the same change.

Repo root (operators usually clone to something like `/home/ubuntu/job_hunt_linkedin`).
SQLite file: **`apply_queue.db`** (gitignored). Python venv is whatever you create
(examples below use `.venv` or `/home/ubuntu/jobhunt-venv`).

---

## 1. Big picture

```
                    ┌──────────── collect ────────────┐
                    │ site_collect.py                 │
                    │ wellfound_fresh.py              │
                    │ scrape_jobs.py (LinkedIn guest) │
                    │ us_startup_collect.py           │
                    └───────────────┬─────────────────┘
                                    │ JSON
                                    ▼
                    inject_site.py / add_fresh_jobs.py
                    (title_filter + canonical URL dedup)
                                    │
                                    ▼
                         apply_queue.db
                    jobs(pending|claimed|done|skip)
                                    │
           ┌────────── atomic claim (one row) ──────────┐
           ▼         ▼          ▼         ▼         ▼
        worker_   worker_   worker_   worker_   worker_
        intern    wellfound linkedin  yc        external
        shala                                     │
           │         │          │         │       ├ himalayas_apply
           │         │          │         │       └ wwr_apply
           └─────────┴──────────┴─────────┴───────┘
                                    │
                    audit.record_application
                    applications table  ← SOURCE OF TRUTH
                                    │
              watchdog.py     portal_guard.py     worker_review.py
              (liveness)      (session live?)     (retry ambiguous)
```

Nothing here is a closed-world UI script. Clicks go through **intent**
(`dynamic_ui` + `learned/selectors.json`). When the page lies, the agent
updates the map. See `docs/AGENT_LOOP.md`.

---

## 2. Data plane

### 2.1 `jobs` table (`apply_queue.db`)

Created by `build_queue.py` / first worker connect. Typical columns:

| Column | Role |
|---|---|
| `id` | primary key (`site-hash` or `wf-12345`) |
| `portal` | worker ownership: `internshala`, `wellfound`, `linkedin`, `yc`, `external` |
| `url` | listing URL |
| `title` | used by `title_filter` |
| `source` | harvest name (`wellfound_python`, `weworkremotely`, …) |
| `status` | `pending` → `claimed` → `done` or `skip` |
| `claimed_by` | worker id (`is-w1`, `wf-w1`, `yc-w1`, …) |
| `result` | machine reason (`applied`, `citizens-only`, `needs-agent:apply`, …) |
| `prio` | higher first (Wellfound 6, Internshala/YC 5, WWR 1) |

**Claim contract:** `SELECT … WHERE portal=? AND status='pending' ORDER BY prio DESC, rowid LIMIT 1` then set `claimed`. One row, one worker. `reset_claims.sh <worker> <portal>` runs on systemd `ExecStartPre` so a killed worker does not leave zombies.

**Never run `build_queue.py` on a live DB.** It `os.remove(DB)`. Use `inject_site.py` / `add_fresh_jobs.py` only.

### 2.2 `applications` table (`audit.py`)

```
id, portal, company, role, url, applied_at, answers, resume_used,
status, note, snap_before, snap_after, url_hash UNIQUE
```

`record_application` dedups on `(portal, url_hash)`. Status we trust: **`submitted`**.

Screenshots land under `audits/` (gitignored).

### 2.3 Identity

`profile.py` loads gitignored `profile_local.py`, else `JOBHUNT_*` env.
`audit.PROFILE` + each worker’s `PROFILE` / `PROFILE_DATA` read from there.

---

## 3. Collect / inject

| Script | What it does | Output |
|---|---|---|
| `site_collect.py` | Cloak/Playwright guest crawl. `SITES`: naukri + many wellfound role/geo slices + internshala + yc (`workatastartup.com/companies?sortBy=created_desc`) | `/tmp/site_collect.json` `[{site, jobs:[{title,link}]}]` |
| `wellfound_fresh.py` | Recency SEO pages, HTTP 200, ≤7 days; seen-db `.wellfound_fresh_seen.db` | JSON for inject |
| `scrape_jobs.py` | LinkedIn **guest** API, checkpointed. Env: `LI_TPR`, `LI_BUDGET`, pause knobs. Cron must **resume**, never delete the checkpoint | `jobs_raw_*.json` |
| `us_startup_collect.py` | Extra startup boards (incl. Himalayas-style URLs) | JSON |
| `indeed_collect.py` / `x_collect.py` / `multisource_pull.py` | Extra feeds | JSON |
| `inject_site.py [file]` | Dedup canonical URL, `is_tech_title`, `INSERT OR IGNORE` | prints added + counts |
| `add_fresh_jobs.py` | Incremental LinkedIn inject; `--prio-bump` | same DB |
| `title_filter.py` | Shared non-tech blacklist (fundraising, fashion, driver, law, …) | bool |

Collector **must** use its own `user_data_dir` (`/tmp/cloak_profile_site`, …) and `headless=True` on a server. Shared `/tmp/cloak_profile` deadlocks workers.

Naukri harvest may return cards; **applying** Naukri from a datacenter IP is parked (Akamai).

---

## 4. Workers (apply plane)

All workers: **CloakBrowser non-pro** driven by **Playwright Python** (`launch_persistent_context(..., executable_path=CLOAK)`). `p.chromium` is Playwright’s protocol name — the process is Cloak, not stock Chromium. Do not `playwright install chromium`. `TMPDIR` on **real disk** (tmpfs kills Cloak). `BrowserWatchdog` (~240s) so a wedged CDP pipe cannot hang forever. Agents repair UIs via **CloakBrowser MCP** + **Playwright MCP**, not by launching Google Chrome. Full stack: `docs/STACK.md`.

They must **sleep on empty**, not `sys.exit`. Exit + `Restart=always` = restart storm (YC bug, fixed). Internshala daily cap counts **today’s** `applications` rows, not lifetime `jobs` applies.

### 4.1 `worker_internshala.py` (`jobhunt-is@is-w1`)
- Portal `internshala`. Cap `IS_DAILY_CAP` (40). Resume upload. Cover from `jd_match`.
- Session: `profiles/is_login`.
- Sleep 30–45m when capped (date-partitioned).

### 4.2 `worker_wellfound.py` (`jobhunt-wf@w1/w2`)
- Dialog-only apply. TrustArc / “WE VALUE YOUR PRIVACY” is **not** the apply modal.
- `APPLY_DIALOG` excludes `#truste` / consent.
- Tries `dynamic_ui.click(page, "wellfound", "apply")` then “Apply Now”.
- Sponsorship / location / citizens gates via `jd_match.BLOCKERS`.
- Category `/role/r/…` pages expand to `/jobs/<id>`.

### 4.3 `worker_linkedin.py` (`jobhunt-li@w1/w2`)
- **Easy Apply only**. External ATS → `no-easy-apply` (correct, ~85% of cards).
- Session `li_state.json`. Select existing resume — do not re-upload copies.
- Relogin: `li_relogin.py` + `li_relogin_loop.sh` (Google chooser / SMS `otp.txt`).

### 4.4 `worker_yc.py` (`jobhunt-yc@w1`)
- Company URL `/companies/<slug>` → expand to `/jobs/<id>` (`done expanded:N`).
- Job page: message modal + Send. Huge `citizens-only` rate is expected.
- **Sleeps** when queue empty.

### 4.5 `worker_external.py` (`jobhunt-ext@w1`)
- Claims `portal='external'`.
- `himalayas` → `himalayas_apply()` using **`profiles/hima_cap`** (cookie JSON values may be empty — do not `add_cookies` a blank jar).
- `weworkremotely` → real ATS href or mailto. `job-copilot` / career-services → `wwr-upsell-not-apply` (not an apply).
- Session-able sources should be **re-tagged** to `wellfound` / `internshala` rather than reimplemented.

### 4.6 `worker_review.py` (`jobhunt-review@r1`)
- Second pass on ambiguous skips: `submit-unconfirmed`, `no-apply-modal`, `fill-err`, …
- Re-runs real apply entry, marks `reviewed|<reason>`.

### 4.7 `worker_guard.py`
- Daemon thread: if a job exceeds `max_sec`, SIGKILL browser tree + worker so systemd restarts.
- Poison-pill: same URL wedging 3× → skip.

---

## 5. Sessions and guards

| Piece | Job |
|---|---|
| `portal_*.json` | Exported cookies (gitignored) |
| `profiles/<name>` | Persistent Chromium dirs |
| `portal_guard.py` | 12h live **probe** (not cookie TTL). `li_at` can look valid for a year and still be revoked. Himalayas `cf_clearance` is fingerprint-bound — **curl always fails**; probe in-browser. Never login while a scrape is running. |
| `li_relogin.py` | Google SSO / GSI chooser / SMS |
| `hima_one_shot.py` / `hima_fill_v9.py` | Himalayas login + onboard |
| `wf_google_login.py` | Wellfound Google |
| `naukri_cap4.py` | Parked capture (needs residential + human cookie export) |

---

## 6. Watchdog and systemd

`watchdog.py` (cron ~5 min, prefer `no_agent`):

- queue empty vs pending mismatch
- `applications` stale (`rate_floor`: 0 submits / 2h with pending > 10)
- **`restart_storm`**: NRestarts jumped ≥15 since last tick
- CPU-spin / claim-stuck → `systemctl restart` with cooldown

Units in-repo:

- `jobhunt-wf@.service` — `Restart=always`, `reset_claims`, reads `wf_password.txt`
- `jobhunt-li@.service` — same shape
- `jobhunt-is@.service` — `Restart=on-failure`, `IS_DAILY_CAP=40`

Also typically installed (not all vendored): `jobhunt-yc@`, `jobhunt-ext@`, `jobhunt-review@`.

`TMPDIR=/home/ubuntu/tmp_chrome` (or your disk path) on every unit.

`launch_workers.sh` — kill/relaunch wf+li. Prefer `systemctl` over `pkill` (self-match hazard).

---

## 7. Matching and honesty

`jd_match.py` (no LLM): extract stack from JD, emit a short honest note, **hard blockers**:

- US citizens / clearance / unpaid / etc. → skip reason string

`sanity_check.py` — golden suite: title_filter, jd_match, audit canonical, DB schema, worker imports. Exit 0/1. Run before you claim the farm is healthy.

---

## 8. Dynamic UI layer

| File | Role |
|---|---|
| `dynamic_ui.py` | `click` / `fill` / `resolve` / `snapshot_a11y` / `report_miss` |
| `ui_intent.py` | thinner helper (same idea) |
| `learned/selectors.json` | living intent map (safe to commit — no secrets) |
| `learned/selectors.example.json` | template |
| `learned/agent_inbox/` | gitignored png+json when an intent misses |

**Supposed to work:** worker asks for intent `apply`. Map walks role/name → text → css. Hit → click. Miss → inbox + `needs-agent:apply`. Agent reads inbox, adds a spec, requeues.

Do not add a one-off hashed class as the only path.

---

## 9. Reports

- `report_status.sh` / `make_report.sh` — markdown snapshot
- Optional 6h Telegram cron — delivery only counts if the doc actually sent

---

## 10. How a new portal is supposed to be added

1. Collect URLs (guest crawl or API) → inject with a `portal=` the worker already owns **or** `external` + `source=`.
2. Prefer retagging to an existing worker over a new 600-line file.
3. Teach intents in `learned/selectors.json` (`apply`, `submit`, `login_wall`, …).
4. Wire `dynamic_ui.click` **before** any CSS.
5. Session capture script writes `profiles/` or `portal_*.json` (gitignored).
6. systemd unit: sleep-on-empty, `reset_claims`, disk `TMPDIR`, watchdog.
7. Document skip reasons. Park if the network (Akamai) makes apply impossible.

---

## 11. Failure modes (expected vs broken)

| Symptom | Likely truth |
|---|---|
| 0 pending, workers sleep | Supply dry — collect |
| Many `citizens-only` | YC/US pool — not a bug |
| Many `no-easy-apply` | LinkedIn density — not a bug |
| `NRestarts` climbing, journal “queue empty — exiting” | Worker still exits on empty — patch to sleep |
| Internshala “cap” with 0 applies today | Cap counted lifetime — must use today’s `applications` |
| Himalayas `Invalid cookie` | Empty JSON jar — use `profiles/hima_cap` |
| WWR `done` on job-copilot | Upsell page — detector must reject |
| `submit-unconfirmed` mass | Consent banner eating clicks |
| Applies stall, workers “running” | Look at `applications`, not PID |

---

## 12. GitNexus

```
node .gitnexus/run.cjs analyze
node .gitnexus/run.cjs status
node .gitnexus/run.cjs query "claim apply queue"
node .gitnexus/run.cjs context claim
node .gitnexus/run.cjs impact applied_today
node .gitnexus/run.cjs detect_changes
node .gitnexus/run.cjs check --cycles
```

Skills under `.claude/skills/gitnexus/`. Root `AGENTS.md` has the GitNexus contract.

Index name: **auto-apply-jobs**. Re-analyze after every commit batch.

---

## 13. File index (apply / ops — not every probe)

| Path | Purpose |
|---|---|
| `worker_*.py` | apply / review |
| `dynamic_ui.py` | intent clicks |
| `audit.py` | applications + PROFILE |
| `profile.py` | identity loader |
| `title_filter.py` / `jd_match.py` | gates |
| `inject_site.py` / `add_fresh_jobs.py` | queue in |
| `site_collect.py` / `wellfound_fresh.py` / `scrape_jobs.py` | harvest |
| `watchdog.py` / `worker_guard.py` / `portal_guard.py` | stay alive |
| `sanity_check.py` | regression |
| `li_relogin.py` / `hima_fill_v9.py` / `naukri_cap4.py` | session (naukri parked) |
| `docs/AGENT_INSTRUCTIONS.md` | paste into an agent |
| `docs/AGENT_LOOP.md` | UI repair tick |
| `docs/ARCHITECTURE.md` | this file |
