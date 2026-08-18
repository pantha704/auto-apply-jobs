# auto-apply-jobs

Multi-source **job application farm**: parallel headless workers claim roles from a SQLite queue and apply on LinkedIn (Easy Apply), Wellfound, Y Combinator, Internshala, Himalayas, and WeWorkRemotely.

No cookies, passwords, or personal identity ship in this repo. You bring your own profile + sessions.

---

## How it fits together

```
  scrape / collect                 apply                         record
 ┌─────────────────┐          ┌──────────────┐            ┌─────────────┐
 │ site_collect.py │          │ worker_yc    │            │ applications│
 │ wellfound_fresh │──inject──│ worker_wf ×2 │──honest───▶│  audit table│
 │ us_startup_     │  queue   │ worker_li ×2 │   skip or  │  screenshots│
 │   collect.py    │          │ worker_is    │   submit   │  watchdog   │
 └────────┬────────┘          │ worker_ext   │            └─────────────┘
          │                   └──────▲───────┘
          ▼                          │
   apply_queue.db ◀──atomic claim────┘
   (pending / claimed / done / skip)
```

Workers **never invent answers**. Unknown questions → skip. US-citizens-only, visa walls, non-tech titles, dead listings → skip.

---

## Requirements

| Need | Version / notes |
|---|---|
| OS | Linux (headless VPS is fine) |
| Python | 3.11+ |
| Browser | [CloakBrowser](https://github.com/CloakHQ/cloakbrowser) or Chromium |
| Packages | `playwright` (and its browsers) |
| Optional | systemd, for supervised workers |

**You also need (never committed):**
- `profile_local.py` — name, email, phone, address
- resume PDF
- logged-in portal sessions (`profiles/`, `portal_*.json`)
- passwords only via env: `GOOGLE_PASSWORD`, `WF_PASSWORD`

---

## 1. Clone and install

```bash
git clone https://github.com/pantha704/auto-apply-jobs.git
cd auto-apply-jobs

python3 -m venv .venv
source .venv/bin/activate
pip install playwright
playwright install chromium
```

Set `CLOAK` in the workers if your Chromium is not at the default path.

---

## 2. Identity (private)

```bash
cp profile_local.example.py profile_local.py
# edit profile_local.py — this file is gitignored

cp .env.example .env
# optional: GOOGLE_PASSWORD, WF_PASSWORD, JOBHUNT_RESUME, IS_DAILY_CAP
```

Answer-bank fields you **must** set honestly (used on forms):

| Field | Meaning |
|---|---|
| years of experience | integer, no inflation |
| current / expected CTC | local currency or USD |
| notice period | days |
| relocate | yes/no |
| education completed | yes/no |
| US work auth | yes/no |
| visa sponsorship needed | yes/no |

Defaults in `audit.py` / workers are **examples**. Change them to *your* truth. The farm would rather skip than lie.

---

## 3. Sessions

Each portal needs a warm browser profile or cookie jar (gitignored):

| Portal | Typical files |
|---|---|
| LinkedIn | `li_state.json` or `profiles/li_login` |
| Wellfound | `portal_wellfound.json` |
| Internshala | `profiles/is_login` |
| YC | existing WAAS SSO profile |
| Himalayas | `profiles/hima_cap` |

Capture helpers (run locally, headed if the site challenges you):

```bash
python capture_yc.py
python wf_google_login.py     # needs GOOGLE_PASSWORD in env
python hima_fill_v9.py        # onboard Himalayas profile
```

Naukri is **parked** (Akamai from datacenter IPs). Don’t expect it to work from a VPS.

---

## 4. Queue and sanity

```bash
python sanity_check.py
# title filter, JD match, DB schema, worker imports
```

Load jobs:

```bash
python site_collect.py                 # writes /tmp/site_collect.json
python inject_site.py /tmp/site_collect.json
# or
python wellfound_fresh.py && python inject_site.py /tmp/wellfound_fresh.json
```

---

## 5. Run workers

```bash
python worker_internshala.py is-w1
python worker_wellfound.py wf-w1
python worker_yc.py w1
python worker_linkedin.py li-w1
python worker_external.py w1          # Himalayas + WWR
python watchdog.py                    # optional loop / cron every 5 min
```

systemd units in-repo: `jobhunt-is@.service`, `jobhunt-wf@.service`, `jobhunt-li@.service`.

Internshala: **40 applies/day**, counted from **today’s** `applications` rows (not lifetime).

YC: sleeps on empty queue (does **not** exit — avoids a systemd crash-loop).

---

## 6. What “applied” means

Source of truth is the `applications` table, **not** `jobs.status='done'`.

| `jobs.result` | Meaning |
|---|---|
| `applied` / audit `submitted` | Real apply |
| `citizens-only` / `sponsorship-block` / `location-block` | Honest skip |
| `no-easy-apply` / `external-or-closed` | LinkedIn not Easy Apply |
| `job-expired` / `category-page` | Bad harvest row |
| `wwr-upsell-not-apply` | WeWorkRemotely career-services page, not an apply |
| `external-apply:https://…` | Opened a real ATS URL (you may still need to finish it) |

---

## 7. Do not commit

```
profile_local.py   .env   wf_password.txt
portal_*.json      li_state.json      profiles/
apply_queue.db     jobs.db            audits/
*.pdf              *.log              state_queue/
```

See `.gitignore`. If you fork this, run a secret scan before the first public push. Old git history can leak even after a later fix — this `main` is a **clean snapshot** with no identity files.

---

## Portals

| Site | In-repo worker | Notes |
|---|---|---|
| Internshala | `worker_internshala.py` | Daily cap |
| Wellfound | `worker_wellfound.py` | TrustArc banner ≠ apply modal |
| YC Work at a Startup | `worker_yc.py` | Many roles are US-citizens-only |
| LinkedIn | `worker_linkedin.py` | Easy Apply only (~10–15% of listings) |
| Himalayas | `worker_external.py` | Needs onboarded talent profile |
| WeWorkRemotely | `worker_external.py` | Mostly external ATS |
| Naukri | — | Parked (Akamai) |

---

## License

MIT. Use only on accounts you own. Sites’ ToS apply — this is your risk.
