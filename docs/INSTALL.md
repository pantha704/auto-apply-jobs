# Install and run (clone)

This is what a public clone can actually do. Anything not listed here is **not** in git and will not appear after `git clone`.

## What you get

- Worker code, dashboard, tests, harvest/inject scripts
- Example identity file (`profile_local.example.py`) and `.env.example`
- No cookies, no résumé, no queue DB, no vault key, no live sessions

## What you need

| Need | Required to | Notes |
|---|---|---|
| Linux, Python 3.11+ | run anything | `python3 -m venv` + pip. No snap/third-party installers. |
| `requirements.txt` | tests + dashboard + workers | `playwright` **Python package only**. Do **not** `playwright install`. |
| `requirements-dev.txt` | pytest | public-clone check |
| CloakBrowser non-pro binary | apply or scrape in a browser | Official Cloak install. Path like `~/.cloakbrowser/chromium-<ver>/chrome`. |
| Your own portal logins | apply | Export/warm sessions locally. Never commit them. |
| systemd + `/var/lib/jobhunt` | unattended farm | Optional. Local can run one worker in the foreground. |

## 1. Clone, venv, verify (no secrets)

```bash
git clone https://github.com/pantha704/auto-apply-jobs.git
cd auto-apply-jobs
git checkout public-v1   # or main — they match as of db31eee+
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
python -m compileall -q .
```

If those two commands pass, the clone is healthy. That is the public-clone bar.

## 2. Local dashboard (no applying)

```bash
JOBHUNT_DASHBOARD_AUTH_DISABLED=1 \
JOBHUNT_CONTROL_DB=/tmp/jobhunt-controlplane.db \
JOBHUNT_VAULT_KEY=/tmp/jobhunt-controlplane.key \
uvicorn controlplane.app:app --host 127.0.0.1 --port 8787
```

Open http://127.0.0.1:8787. Production **must** use Basic Auth and must not bind this to the public internet. See [CONTROL_PLANE.md](CONTROL_PLANE.md).

## 3. Identity and résumé (required before any apply)

```bash
cp profile_local.example.py profile_local.py   # gitignored
cp .env.example .env
```

Fill only facts you would type yourself. Workers skip rather than invent.

Dashboard path: Candidate page → upload résumé → check extracted email/phone → save/approve profile and a pay policy. Workers claim only after a published profile + active policy exist.

## 4. Browser

Install CloakBrowser non-pro from the vendor. Then:

```bash
export CLOAK="$HOME/.cloakbrowser/chromium-<version>/chrome"
test -x "$CLOAK"
```

Do not install stock Chromium via `playwright install`.

## 5. Sessions

Warm each portal in Cloak/Playwright and keep the session **outside git** (`.private/sessions` or `profiles/`). Naukri is parked (datacenter IP / Akamai).

Without a valid session the worker stops before claim.

## 6. One worker, foreground

```bash
export JOBHUNT_QUEUE_DB="$PWD/apply_queue.db"
export JOBHUNT_CONTROL_DB="$PWD/controlplane.db"
python worker_internshala.py is-w1
```

Collect/inject first (`python site_collect.py` then `python inject_site.py /tmp/site_collect.json`) or you will sit on an empty queue.

## 7. Production farm (this VPS shape)

Not required for a laptop clone.

- DBs and key: `/var/lib/jobhunt`, `/etc/jobhunt/canonical-runtime.env`
- systemd units: `jobhunt-li@`, `jobhunt-wf@`, `jobhunt-is@`, `jobhunt-yc@`, `jobhunt-ext@`, `jobhunt-email@`, `jobhunt-review@`, `jobhunt-dashboard`
- Harvest crons live in Hermes (`jobhunt-fresh-24h-hourly`, `jobhunt-refill`, `jobhunt-inject`)

## 8. Pause the whole farm

```bash
python jobhunt_farm.py status
python jobhunt_farm.py pause    # stop apply workers; harvest/watchdog go silent
python jobhunt_farm.py resume
python jobhunt_farm.py shutdown # workers + dashboard + session probes
```

Dashboard top bar also has **Pause farm**, **Resume farm**, and **Shutdown**. Dashboard stays up on pause. Shutdown closes the dashboard too. Pause/shutdown **never delete** portal sessions, browser profiles, or queue DBs.

## Agent docs

If an AI is operating the repo, give it [AGENT_INSTRUCTIONS.md](AGENT_INSTRUCTIONS.md) and [AGENT_LOOP.md](AGENT_LOOP.md) after this file. It must not invent answers or commit secrets.

## Quick reference: Makefile

```bash
make help          # list all targets
make install-dev   # pip install requirements.txt + requirements-dev.txt
make check         # lint + test (what CI runs)
make test          # pytest tests/
make dashboard     # local dashboard on :8787
```

## Quick reference: Docker

```bash
make docker        # build image
make docker-run    # run dashboard in container on :8787
```

Or directly:

```bash
docker build -t jobhunt-linkedin .
docker run --rm -p 8787:8787 -e JOBHUNT_DASHBOARD_AUTH_DISABLED=1 jobhunt-linkedin
```
