# Production execution: implementation and release plan

Status: implemented reliability foundation; live portal certification still required.
Validated locally on Python 3.12: 282 tests passed, Python undefined-name checks,
JavaScript syntax and pip dependency consistency passed. One upstream Starlette/
AnyIO deprecation warning remains. No real applications or emails were sent.

## Architecture decision

Keep Python, SQLite and Playwright for the current single-host, single-applicant
system. Rewriting the UI or adopting distributed infrastructure would not fix the
main failure: a lost response after Submit could become a duplicate application.
The central abstraction is now a durable submission intent, committed before the
external action. An unknown outcome requires reconciliation, never an automatic
retry. This trades some throughput for safer behavior.

SQLite transactions protect local state, but cannot make a browser click and a
remote server transaction atomic. Exactly-once delivery cannot be promised where
the destination provides no idempotency key or authoritative receipt endpoint.
Likewise, a crash between intent commit and the click conservatively quarantines
an application that might never have been sent.

## What changed

| Area | Implemented behavior |
|---|---|
| Submission | Unique job/application intent, live lease and owner fence, identity check, release revalidation before clicking |
| Completion | Queue, audit, run and attempt complete in one transaction; confirmation artifact hash checked |
| Recovery | Expired preparation can requeue; dispatched or ambiguous work is quarantined; database trigger blocks requeue |
| Candidate data | Resume bytes pinned; exact approved answers; unknown custom questions and unsupported work history stop for review |
| Sessions | Exact revision injection, origin-scoped local storage, monotonically increasing renewal fences, stale health probes ignored |
| Email | Approved recipient frozen with draft; attempt tokens prevent stale completion; uncertain delivery remains unknown |
| Queue ingestion | Non-destructive migration and insert; existing claims/history preserved |
| Operations | Fresh setup starts paused; lost vault key blocks setup; service failures returned truthfully |
| Dashboard | API field alignment, boolean handling, timeline, portal counts and trace actions corrected |
| Analytics | New telemetry tables no longer hide historical confirmations; run success rate uses the run cohort |
| CI | Dependency constraints, Python 3.11/3.12 matrix, tests, undefined names and JavaScript syntax |

## Installation and migration

1. Stop all workers, harvesters, reviewers and watchdog timers. Set the farm pause
   flag before migration. Verify processes have actually stopped; a dashboard
   success message alone is insufficient if systemd reports errors.
2. Back up both SQLite databases using SQLite's backup API (not a plain file copy
   while WAL writers are active). Back up the vault key, encrypted session bundles,
   resumes and receipt artifacts together. Store backups outside the repository.
3. Install the branch into an isolated virtual environment:

   ```sh
   python3 -m venv .venv
   .venv/bin/pip install -c requirements.lock -r requirements.txt -r requirements-dev.txt
   .venv/bin/pip check
   ```

4. Configure `JOBHUNT_QUEUE_DB`, `JOBHUNT_CONTROL_DB`, `JOBHUNT_VAULT_KEY`,
   `JOBHUNT_RESUME_STORAGE`, `JOBHUNT_SESSION_STORAGE`, `JOBHUNT_PAUSE_FLAG` and
   dashboard authentication in the service environment. Run:

   ```sh
   .venv/bin/python -m workflow.bootstrap
   .venv/bin/python -m pytest -q
   ```

   Queue schema is version 6; control schema is version 8. Existing queued email
   drafts require recipient reapproval because old records did not preserve the
   originally approved address. Setup never resumes sending. Repeated setup
   preserves existing data and the encryption key. Missing key on an existing
   control database is a restore operation, not a key-regeneration operation.

5. Install and verify the approved CloakBrowser binary on the deployment host.
   Set `JOBHUNT_BROWSER_EXECUTABLE` explicitly for repeatable deployments. The
   Python dependency constraints do not pin or install the browser binary.
6. Approve the actual candidate profile, resume, non-empty preference policy and
   portal session. Check all mandatory facts for the selected adapter. Keep sending
   disabled during inspection and dry runs.
7. For rollback, stop writers and restore the matched pre-migration databases,
   key and private files with the old code. Do not run old workers against the new
   submission journal: they do not enforce its invariants.

## Remaining work, in execution order

### 1. Certify one narrow portal flow

Start with one known job form on one portal, one worker and a low daily cap.
Capture sanitized fixtures for normal, closed, already-applied, logged-out,
validation-error, CAPTCHA, delayed receipt and changed-layout states. Verify
request/response and page evidence without sending real applications during tests.

The current generic confirmation signal plus screenshot is not an authoritative
remote receipt. Add portal-specific receipt parsing correlated to requisition and
account identity, and validate both false-positive and false-negative cases. The
strict job URL check intentionally stops uncertified redirect flows. Explicitly
map ATS redirects to a requisition before allowing them. Resume upload currently
requires an observable file input; saved-profile-only resumes are not certified.

Acceptance: local browser fixtures cover the entire adapter, then an explicitly
approved live canary is independently visible in the portal application history.
Do not enable all portals just because one succeeds.

### 2. Complete the review loop

Expose unknown outcomes with their frozen release, job identity, event timeline
and receipt evidence. An operator must distinguish confirmed sent, definitely not
sent and still unknown. Record reviewer identity, time, evidence and decision.
Do not provide an unguarded retry button or delete submission intents. A future
retry authorization needs its own durable attempt identity and proof that the
previous attempt was not accepted. The current reviewer labels ambiguous work for
reconciliation; it does not resubmit it.

Add candidate UI fields for every adapter's required facts and an exact-question
answer approval workflow. Structured work history needs a proper approved schema,
not hardcoded dates or company names. An absent fact should remain unknown.

### 3. Exercise the actual deployment

Run a clean-host installation using the pinned dependencies and intended browser.
Verify systemd permissions, restart behavior, pause persistence, authentication,
private artifact access and disk limits. Kill processes before intent, after
intent, after click and after receipt, including real subprocess termination.
Inject SQLite contention, full disk, unavailable browser and broken network.
Restore a backup into an isolated host and verify it can decrypt sessions and
resumes. Current tests use simulated failures and temporary databases; a VPS
restore drill and a real-browser compatibility run have not been performed.

### 4. Operate with measurable limits

Measure confirmed submissions / attempts, unknown outcomes, blocked questions,
session expiry, queue age, worker heartbeat, portal latency and disk usage.
Alert on unknown outcomes immediately; pause an adapter after repeated layout or
receipt failures. Use bounded retries only for operations proved not to have sent.
Set retention for PII-bearing screenshots and resumes; test deletion and backup
retention together. Run a multi-day limited canary before unattended operation.

### 5. Expand deliberately

Certify additional portals separately. Remove duplicate legacy orchestration only
after contract tests cover the replacement path. Move to PostgreSQL and a durable
workflow service when adding multiple hosts or tenants, with tenant isolation,
authorization and distributed concurrency tests. Do not share a SQLite database
across network filesystems or assume this single-applicant design is a SaaS.

## Regression evidence

`tests/test_production_dispatch.py` covers competing dispatches, invalid and expired
leases, identity mismatch, pause, post-dispatch requeue prevention, crash recovery,
stale workers, missing/tampered receipts, atomic completion, click timeout,
changed resume bytes and historical duplicate detection.

`tests/test_production_operations.py` covers frozen email recipients, stale email
attempts, session fencing, stale probes, historical analytics, repeatable paused
setup and refusal to replace a missing vault key.

This foundation reduces known failure modes. It does not establish zero failures,
verify every live portal, or authorize unattended sending.
