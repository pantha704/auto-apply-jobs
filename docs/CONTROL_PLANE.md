# Web control plane

The control plane turns the existing job-application farm into an operator-facing product. It provides onboarding, site configuration, readiness checks, live queue and worker status, issues, application history, and retained worker telemetry.

## Stack

- **Backend:** Python 3.11+, FastAPI, Uvicorn
- **UI:** self-contained HTML/CSS/JavaScript SPA; no Node build required
- **Operational database:** existing `apply_queue.db` (queue + application source of truth)
- **Control database:** `controlplane.db` (sites, encrypted profile fields, events, telemetry)
- **Encryption:** `cryptography.fernet`; key is a mode-0600 host file
- **Process supervision:** systemd (`jobhunt-dashboard.service`)
- **Worker status:** exact allowlisted `systemctl show` calls + psutil process metrics
- **Browser data plane:** unchanged CloakBrowser non-pro + Python Playwright workers

## Pages

| Page | Purpose |
|---|---|
| Overview | Confirmed submissions, live queue, active workers, readiness, portal distribution |
| Sites | Add website URL, auth method, encrypted credential/session reference, ATS adapter |
| Onboarding | Encrypted required profile fields, résumé and site readiness checklist |
| Workers | Live systemd state, PID, CPU, RSS memory, restart count, restart control |
| Issues | Blocking onboarding failures and grouped operational skip patterns |
| History | Paginated, redacted application audit history |

## Environment

Production values belong in `/etc/jobhunt/job-hunt.env`:

```bash
JOBHUNT_DASHBOARD_USER=operator
JOBHUNT_DASHBOARD_PASSWORD=<strong-random-password>
JOBHUNT_QUEUE_DB=/var/lib/jobhunt/apply_queue.db
JOBHUNT_CONTROL_DB=/var/lib/jobhunt/controlplane.db
JOBHUNT_VAULT_KEY=/etc/jobhunt/controlplane.key
JOBHUNT_RESUME=/home/ubuntu/job_hunt_linkedin/resume.pdf
```

`JOBHUNT_DASHBOARD_AUTH_DISABLED=1` is for local automated tests only. Never use it on a network listener.

## Local development

```bash
python -m pip install -r requirements.txt
JOBHUNT_DASHBOARD_AUTH_DISABLED=1 \
JOBHUNT_CONTROL_DB=/tmp/jobhunt-controlplane.db \
JOBHUNT_VAULT_KEY=/tmp/jobhunt-controlplane.key \
uvicorn controlplane.app:app --host 127.0.0.1 --port 8787
```

Open `http://127.0.0.1:8787`.

## Production deployment

```bash
sudo install -d -o ubuntu -g ubuntu -m 0700 /var/lib/jobhunt
sudo install -d -o root -g ubuntu -m 0750 /etc/jobhunt
key_tmp=$(mktemp)
/home/ubuntu/jobhunt-venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' > "$key_tmp"
sudo install -o ubuntu -g ubuntu -m 0600 "$key_tmp" /etc/jobhunt/controlplane.key
rm -f "$key_tmp"
sudo visudo -cf jobhunt-dashboard.sudoers
sudo install -o root -g root -m 0440 jobhunt-dashboard.sudoers /etc/sudoers.d/jobhunt-dashboard
sudo install -o root -g root -m 0644 jobhunt-dashboard-firewall.nft /etc/jobhunt/dashboard-firewall.nft
sudo install -m 0644 jobhunt-dashboard-firewall.service /etc/systemd/system/
sudo install -m 0644 jobhunt-dashboard.service /etc/systemd/system/jobhunt-dashboard.service
sudo systemctl daemon-reload
sudo systemctl enable --now jobhunt-dashboard.service
```

The vault key is provisioned before startup; the web process cannot write `/etc/jobhunt` or the source repository. The provided unit binds `0.0.0.0:8787` but has a fail-closed `Requires=` dependency on the companion nftables service, which permits only loopback and `tailscale0`. Basic authentication is still mandatory. Unsafe API methods additionally require the UI's `X-Jobhunt-CSRF: 1` header and reject foreign origins. The sudoers file contains exact commands for the eight registered workers and no wildcard unit names. A dedicated privileged broker or polkit service is the preferred replacement before multi-user hosting.

Queue indexes are never created by web startup. An operator may run the schema-aware maintenance step explicitly:

```bash
sudo -u ubuntu env JOBHUNT_QUEUE_DB=/var/lib/jobhunt/apply_queue.db \
  /home/ubuntu/jobhunt-venv/bin/python -m controlplane.migrate
```

Production keeps the authoritative queue under `/var/lib/jobhunt`. Legacy workers may retain a gitignored compatibility symlink at `<repo>/apply_queue.db`; both paths must resolve to the same inode. Stop all workers during the one-time SQLite backup/cutover, verify `PRAGMA integrity_check` and table counts, then restart them together.

## API

| Method | Endpoint | Behavior |
|---|---|---|
| GET | `/livez` | Unauthenticated process liveness only |
| GET | `/readyz` | Unauthenticated generic DB/key readiness; HTTP 503 on failure |
| GET | `/api/health` | Authenticated readiness status without paths or secrets |
| GET | `/api/overview` | Queue, confirmed applications, readiness, workers |
| GET/POST | `/api/sites` | List/add site configurations |
| DELETE | `/api/sites/{id}` | Remove site configuration |
| PUT | `/api/profile` | Encrypt and store required profile fields |
| GET | `/api/profile/status` | Completeness only; never profile values |
| GET | `/api/readiness` | Typed blocking issues and remediation |
| GET | `/api/workers` | Live systemd/process metrics |
| GET | `/api/workers/{unit}/history` | Retained uptime/resource samples |
| POST | `/api/workers/{unit}/{action}` | Exact allowlist; `start`, `stop`, `restart` |
| GET | `/api/issues` | Setup blockers and operational skip groups |
| GET | `/api/applications` | Paginated redacted history |

## Credential and profile handling

- The browser never receives stored secrets through a status API.
- Site usernames and passwords are encrypted before SQLite insertion.
- API responses include only `credential_configured` and a masked username.
- Required profile fields are encrypted individually. Status responses expose field names and completeness, not values.
- On first production startup, the control plane explicitly loads the repository's `profile.py`, which resolves the gitignored `profile_local.py`; set `JOBHUNT_PROFILE_BOOTSTRAP=0` to disable this or `JOBHUNT_PROFILE_MODULE` to choose another loader.
- Session references point to local persistent profiles; session/cookie contents are not copied into the control DB.

## Readiness model

A configured site is blocked when any of these are true:

- required applicant fields are missing;
- résumé path is missing;
- an adapter cannot be resolved;
- no site is enabled;
- password authentication lacks either username or password;
- session authentication lacks an existing local session/profile path.

Later generic-engine phases will add session probes, answer-bank coverage, network reachability, and adapter capability checks.

## Telemetry and retention

The FastAPI lifespan starts a one-minute worker sampler. It stores active state, CPU, RSS memory, restart count, and timestamp in `worker_samples`. Samples older than 30 days are deleted. Application truth remains in `apply_queue.db`; telemetry is not evidence of a submitted application.

## Product architecture roadmap

1. **Control plane** — current implementation.
2. **Typed site manifests** — normalize onboarding records into versioned capabilities and policies.
3. **Reusable ATS adapters** — Greenhouse, Lever, Ashby, Workday, SmartRecruiters, generic HTML.
4. **Workflow engine** — typed page states/actions, retries, leases, postconditions, review escalation.
5. **Answer bank** — versioned, user-approved answers; unknown questions create issues.
6. **Assisted login and repair** — copied browser profiles through loopback CloakBrowser CDP, with MCP reserved for deep diagnostics.
7. **Multi-user isolation** — tenant-scoped DB rows, vault keys, profiles, queues, audit retention.

The next schema migration should introduce versioned migrations, `site_accounts`, `readiness_checks`, append-only `application_runs`/`job_attempts`, lease expiry and retry fields, worker-instance heartbeats, typed outcome codes, retained artifacts, and metric rollups. Existing `jobs` and `applications` remain compatibility projections until every worker uses the shared workflow engine.

See `docs/ARCHITECTURE.md` for the complete existing data plane and integration boundaries.
