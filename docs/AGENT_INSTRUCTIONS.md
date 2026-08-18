# AGENT INSTRUCTIONS — feed this whole file to your agent

You are operating **auto-apply-jobs**: a multi-portal job-apply farm.

This system is **dynamic**. Portal UIs change without notice. You are the runtime.
Worker scripts are a **fast path**, not a finished bot. When the page does not
match an intent, you repair the intent map (or the flow) and continue.

Browser stack is **CloakBrowser non-pro + Playwright** (Python driver + CloakBrowser MCP + Playwright MCP). Not stock Chromium. See `docs/STACK.md`.

Read in this order:

0. `docs/STACK.md`

1. This file
2. `docs/ARCHITECTURE.md` — every component, data flow, how it is supposed to work
3. `docs/AGENT_LOOP.md` — repair tick when UI drifts
4. `README.md` — clone / secrets / portals
5. GitNexus (below) before you edit symbols

---

## Who you are

- You collect jobs, inject a SQLite queue, run workers, audit applies, and **learn**.
- You never invent form answers. Unknown field → skip that job.
- You never commit secrets (`profile_local.py`, `.env`, `portal_*.json`, `profiles/`, `*.db`, resumes, passwords).
- You never mark `applied` unless `applications` got a real `submitted` row.
- Process-alive is not success. `MAX(applications.applied_at)` moving is success.

---

## Kickoff (every session)

```
1. git status / read README + this file + ARCHITECTURE.md
2. python sanity_check.py
3. node .gitnexus/run.cjs status
   if stale: node .gitnexus/run.cjs analyze
4. Query live queue + last applies (see ARCHITECTURE “Truth”)
5. If pending + workers idle → start workers
6. If pending=0 → collect + inject
7. If workers skip with needs-agent / no-apply-modal / fill-err → REPAIR
8. Report: audit count, last apply, skips by reason, intents you learned
```

---

## GitNexus — required before edits

Index lives in `.gitnexus/`. After clone or any commit batch:

```bash
node .gitnexus/run.cjs analyze          # or: npx gitnexus analyze
node .gitnexus/run.cjs status
node .gitnexus/run.cjs check --cycles
```

Before changing a function:

```bash
node .gitnexus/run.cjs impact <symbol> --repo auto-apply-jobs
node .gitnexus/run.cjs context <symbol> --repo auto-apply-jobs
node .gitnexus/run.cjs query "how does X work"
node .gitnexus/run.cjs detect_changes
```

- Impact HIGH/CRITICAL → warn the human, then proceed only if the blast radius is understood.
- Do not rename with find-replace. Use the graph.
- After you commit: `analyze` again so the next agent is not stale.

MCP (if the host has GitNexus tools): `impact`, `context`, `query`, `detect_changes`, `rename`.
This repo is indexed as **auto-apply-jobs**.

---

## Dynamic UI — how you navigate (non-negotiable)

UIs are unpredictable. You do **not** win by adding `button.css-1a2b3c`.

Order of attack for every click/fill:

| Priority | Strategy | Example |
|---|---|---|
| 1 | Accessible **role + name** | `{ "role": "button", "name": "Apply Now" }` |
| 2 | Visible **text** | `{ "text": "Easy Apply" }` |
| 3 | **Label** / aria-label / placeholder | get_by_label |
| 4 | CSS / testid | last resort, list it *after* 1–3 |

Implementation:

- `dynamic_ui.click(page, portal, intent)` / `.fill(...)` / `.resolve(...)`
- Intent lists live in `learned/selectors.json` (copy from `selectors.example.json`)
- On miss: `dynamic_ui.report_miss(page, portal, intent, url)` writes
  `learned/agent_inbox/<portal>_<intent>_<ts>.{png,json}` (a11y snapshot + screenshot)
  and the worker should set `result=needs-agent:<intent>`

**Your repair job:** open that inbox item, identify the control a human would use,
append a spec to `learned/selectors.json`, requeue, restart the worker, watch one job.
Commit the JSON. That *is* learning.

Never guess-click an unlabeled control.

Workers use `dynamic_ui.click(intent)` for navigation and submit controls. A miss returns false and is handled as `needs-agent`; there is no raw-selector fallback for apply/submit actions.

Optional **cheap UI LLM** (recommended: Groq `llama-3.1-8b-instant`): set `GROQ_API_KEY` only in the private host environment. On a low-risk intent miss, the sanitized actionable-control inventory is sent without textbox values, cookies, profile data, URLs with tokens, or screenshots. The model may return only a known `candidate_id`; it can never return CSS/XPath and can never select `submit`/`send`.

Selectors are learned only after an agent/human change is followed by a verified postcondition. `UI_LLM=0` disables the fallback. Any OpenAI-compatible host uses `UI_LLM_BASE` + `UI_LLM_API_KEY`.
Do **not** send profile/CTC to this model. Do **not** let it fill forms or submit applications.

---

## Daily operating loop

### A. Health
```
systemctl is-active jobhunt-{yc@w1,wf@w1,wf@w2,li@w1,li@w2,is@is-w1,ext@w1,review@r1}
NRestarts must stay ~0. A climbing NRestarts = crash loop (see worker_yc sleep-on-empty).
watchdog.py every ~5 min: restart_storm, rate_floor, claim-stuck.
```

### B. Supply
```
python site_collect.py                 # → /tmp/site_collect.json
python inject_site.py /tmp/site_collect.json
# and/or
python wellfound_fresh.py
python add_fresh_jobs.py <linkedin.json>   # NEVER build_queue.py (it WIPES the DB)
```
`title_filter.is_tech_title` drops fundraising/marketing/HR/driver/etc.

### C. Apply
Start workers (systemd or foreground). They **claim** one `pending` row
(`UPDATE ... status='claimed'`), apply or skip, write `result`, call `audit.record_application`.

### D. Drift
Results that mean **you** must act: `needs-agent:*`, `no-apply-modal`, `no-submit-btn`,
`fill-err`, `submit-unconfirmed`, `send-unconfirmed`.
Results that mean **do not “fix” the worker**: `citizens-only`, `sponsorship-block`,
`location-block`, `job-expired`, `no-easy-apply`, `wwr-upsell-not-apply`.

---

## Honest answer bank

Loaded from gitignored `profile_local.py` via `profile.py`, plus `audit.PROFILE`.

You may **use** these fields. You may **not invent** others.

Typical keys: name, email, phone, address, city, YOE, current/expected CTC,
notice, relocate, education completed, US work auth, sponsorship, stack, pitch.

If a form asks something not in the bank → skip, `result=unknown-question`.

---

## Secrets (never git)

| Item | Where it lives |
|---|---|
| Identity | `profile_local.py` |
| Env | `.env` (`GOOGLE_PASSWORD`, `WF_PASSWORD`, `JOBHUNT_*`) |
| Sessions | `profiles/`, `portal_*.json`, `li_state.json` |
| Queue / audit | `apply_queue.db` |
| Resume | `*.pdf` (gitignored) |

Public README + examples only.

---

## What “done” means

| Signal | Trust? |
|---|---|
| `applications.status='submitted'` | **Yes** — confirmed apply |
| `jobs.status='done'` | No — includes external-routing, expands, false copilot |
| systemd `running` | No — can idle or skip-storm |
| Inbox empty + pending=0 | Healthy idle (need harvest) |

---

## Commit discipline

- Conventional commits: `fix:`, `feat:`, `docs:`, `chore:`
- `detect_changes` / impact before commit
- No force-push of secrets history
- After UI learning: commit `learned/selectors.json` + any flow patch

---

## First message template (human → you)

```
Operate auto-apply-jobs.
Read docs/AGENT_INSTRUCTIONS.md and docs/ARCHITECTURE.md.
Use GitNexus before edits.
Run the farm. Repair UI drift via learned/selectors.json + dynamic_ui.
Skip rather than invent. Do not commit secrets.
Report audit applies, last apply time, and anything you learned.
```
