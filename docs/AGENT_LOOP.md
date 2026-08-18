# Agent loop — how this farm stays alive when UIs change

This is the operating manual. If you are an agent (Hermes, Claude Code, Codex, OpenCode, …), **follow this file**, not a memory of last month’s CSS.

Hardcoded selectors are a **cache**. You are the **runtime**.

---

## Standing rules

1. Prefer **intent** over class names: role, accessible name, visible text, then CSS.
2. If the page does not match the intent map → **do not guess-click**. Snapshot and learn.
3. Never invent form answers. Skip the job.
4. Never commit `profile_local.py`, `.env`, `portal_*.json`, `profiles/`, `*.db`, resumes.
5. `applications.applied_at` moving is the only proof of applies. Process-alive is not.

---

## Tick (every run)

```
A. Health
   - sanity_check.py
   - workers: running? NRestarts storm? journal errors?
   - last real apply time

B. Supply
   - site_collect / wellfound_fresh / inject
   - drop non-tech titles (title_filter.py)
   - drop expired / category URLs

C. Apply
   - start or resume workers
   - they read learned/selectors.json first, then built-in fallbacks

D. Drift
   - if result in {needs-agent, no-apply-modal, no-submit-btn,
     fill-err, submit-unconfirmed, send-unconfirmed}:
       go to REPAIR
```

---

## Repair (self-learning)

When a portal UI drifted:

1. **Reproduce one URL** from the queue (`SELECT url, result FROM jobs WHERE result LIKE 'needs-agent%' OR result LIKE 'no-apply-modal%' LIMIT 1`).
2. Open it headed or headless with the **same profile** the worker uses.
3. Capture:
   - screenshot (`audits/` or `/tmp/agent_*.png`)
   - accessibility snapshot (roles + names, not raw HTML dump)
   - the button/text a human would click
4. Update **`learned/selectors.json`**:
   - key = intent (`apply`, `submit`, `dismiss_consent`, `easy_apply`, `login_wall`, …)
   - values = list of strategies, most stable first:
     `{ "role": "button", "name": "Submit application" }`
     `{ "text": "Easy Apply" }`
     `{ "css": "[data-test=apply]" }`  ← last resort
5. Only if the intent map cannot express the step, patch the worker’s **control flow** (new modal, extra page). Do not add a one-off hex class.
6. Requeue those rows (`status=pending`, clear `result` / `claimed_by`).
7. Restart that worker so it loads the new map.
8. Watch one live apply or an honest skip.
9. **Commit the learning** (`learned/selectors.json` + any worker flow change). That is the memory.

---

## Intent map shape

See `learned/selectors.example.json`. Copy to `learned/selectors.json` (safe to commit if it has **no secrets**).

```json
{
  "wellfound": {
    "dismiss_consent": [
      { "role": "button", "name": "Reject All" },
      { "text": "Agree & Proceed" }
    ],
    "apply": [
      { "role": "button", "name": "Apply" }
    ],
    "submit": [
      { "role": "button", "name": "Submit" }
    ]
  }
}
```

Workers should walk the list until one is visible. If none are, they must exit that job as `needs-agent:<intent>` plus a screenshot.

---

## Kickoff prompt (paste into any agent)

```
Repo: auto-apply-jobs.
Read README.md and docs/AGENT_LOOP.md.
Operate the farm: collect, inject, run workers, repair UI drift via
learned/selectors.json. Do not hardcode a single CSS class as the only
apply path. Skip rather than invent answers. Do not commit secrets.
Report: confirmed applications (audit table), skips by reason, and any
intents you learned this session.
```

---

## When *not* to “learn”

- US-citizens-only / sponsorship / location → skip, not a UI bug
- Expired listing / HTTP 422 → harvest problem, not a selector problem
- Internshala daily cap → wait for UTC midnight
- Naukri Akamai 403 from a datacenter IP → parked; need a residential session
- Empty queue → collect more, don’t restart-storm

---

## Watchdog

`watchdog.py` (every ~5 min): restart storms, empty-queue-with-pending, rate floor.

If it only says “workers running” and `applications` has not moved, **you** still have work (supply or drift).
