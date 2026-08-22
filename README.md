# auto-apply-jobs

A **multi-portal job-apply farm** meant to be **run by an AI agent**, not a pile of frozen CSS selectors.

Sites change their UI every week. Hardcoded `button.easy-apply` **will** rot. This repo’s contract is:

1. Workers try a **small, intent-based** apply (role/name/text, not one class name).
2. On mismatch they **stop and ask an agent** (screenshot + DOM + reason) instead of clicking the wrong thing.
3. The agent **patches the intent map** (or the worker) and requeues. That patch *is* the learning.

Humans set identity + sessions. Agents run, watch, and repair.

## Web control plane

The repository now includes a responsive FastAPI control plane for people who should not need to operate Python scripts, SQLite, or systemd directly.

- add websites and select/auto-detect ATS adapters;
- store credentials and applicant profile fields encrypted at rest;
- see exactly what setup information is missing;
- monitor queues, confirmed submissions, workers, CPU/memory, restarts, and retained uptime samples;
- inspect grouped issues and redacted application history;
- restart only exact allowlisted worker units.

```bash
JOBHUNT_DASHBOARD_AUTH_DISABLED=1 uvicorn controlplane.app:app --host 127.0.0.1 --port 8787
```

Production must use Basic Auth and tailnet/firewall exposure. See [docs/CONTROL_PLANE.md](docs/CONTROL_PLANE.md) for the stack, APIs, deployment, security boundaries, and generic-ATS roadmap. The complete system blueprint is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## How it fits together

```
 applicant setup                  workflow router
 ┌──────────────┐        ┌─────────────────────────────────────┐
 │ profile truth│        │ verified recipe → Playwright fast   │
 │ resume       │───────▶│ drift/unknown → Browser Use discover│
 │ portal login │        │ deep repair → MCP diagnostics       │
 └──────────────┘        └──────────────────┬──────────────────┘
                                            ▼
                                   CloakBrowser over CDP
                                            │
                          verify postcondition + atomic audit
                                            ▼
                            /var/lib/jobhunt/apply_queue.db
                               applications (source of truth)
                               versioned learned recipes
```

The deployed workers currently use Python Playwright with CloakBrowser plus the intent layer in `dynamic_ui.py`. The Browser Use router and recipe compiler shown above are the **target architecture under implementation**, not a claim that every worker has already migrated. Known workflows stay deterministic; Browser Use is reserved for discovery and recovery; MCP is the supervised diagnostic escalation path.

**Do not treat worker `*.py` as a finished universal bot.** Treat them as current fast paths being migrated behind the shared workflow contract. The README + `docs/AGENT_LOOP.md` are how a stranger (or Hermes / Claude Code / Codex / OpenCode) operates the farm.

---

## Requirements

| Need | Notes |
|---|---|
| Linux | headless VPS is fine |
| Python 3.11+ | venv + `pip install playwright` (**driver only**) |
| **CloakBrowser non-pro** | Binary at `~/.cloakbrowser/chromium-<ver>/chrome`. **Not** stock Chromium. |
| Playwright Python | Wires to Cloak via `executable_path=CLOAK`. Do **not** `playwright install chromium`. |
| Browser execution | CloakBrowser is the only Chromium implementation. Python Playwright runs verified recipes; Browser Use over loopback CDP is the planned discovery/recovery layer. |
| Agent tooling | Playwright CLI + skills for token-efficient inspection/replay. CloakBrowser MCP and Playwright MCP are optional deep-diagnostic tools, not equal primary executors. |
| An **agent** | Hermes, Claude Code, Codex, OpenCode — must read docs/AGENT_INSTRUCTIONS.md |
| Optional | systemd, 5‑min watchdog cron, `GROQ_API_KEY` |

**Never in git:** `profile_local.py`, `.env`, cookies (`portal_*.json`, `profiles/`), DBs, resumes, passwords.

---

## 1. Clone and install

```bash
git clone https://github.com/pantha704/auto-apply-jobs.git
cd auto-apply-jobs
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
# Do not run `playwright install`; CloakBrowser supplies the executable.
# Install CloakBrowser non-pro, then:
export CLOAK="$HOME/.cloakbrowser/chromium-<version>/chrome"
# Optional diagnostics: enable CloakBrowser MCP / Playwright MCP.
# The target router attaches Browser Use and Playwright CLI to CloakBrowser over loopback CDP.

# Pure public-clone verification (no live profile, cookies, queue, or portal):
python -m pytest -q
python -m compileall -q .
```
See [docs/STACK.md](docs/STACK.md) for the exact browser stack.

---

## 2. Identity (you, once)

```bash
cp profile_local.example.py profile_local.py   # gitignored
cp .env.example .env
# put YOUR honest YOE / CTC / notice / relocate / sponsorship answers
```

Workers skip rather than invent. Fill the truth.

---

## 3. Let an agent run it (this is the default)

Point an agent at this repo and give it **`docs/AGENT_LOOP.md`** as the standing order.

Example kickoff prompt:

> You are operating auto-apply-jobs. Read README.md and docs/AGENT_LOOP.md.
> Do not hardcode new CSS classes as the only path.
> Collect → inject → run a verified recipe. On drift, gather a privacy-safe
> candidate inventory and use Browser Use only for discovery/recovery through
> CloakBrowser. Require typed actions and a portal postcondition, then compile
> the proven sequence into a versioned deterministic recipe. Never let a model
> invent answers, select submit/send, or mark an application submitted. Never
> commit secrets.

That is how the farm **self-learns**: each UI break becomes a postcondition-verified recipe revision, not a one-off “fix it in chat”.

### What the agent is allowed to change
- `learned/selectors.json` — intent keys (`apply`, `submit`, `dismiss_consent`, `easy_apply`, …)
- worker control flow when a portal adds a new step
- title filters / skip reasons (honest)

### What the agent must not do
- commit cookies, `.env`, `profile_local.py`, DBs
- click a guessed button with no label
- mark `applied` unless the audit row is real
- answer a form field that is not in the honest bank → **skip**

---

## 4. Fast path (optional, no agent)

If a portal’s current UI still matches:

```bash
python sanity_check.py
python site_collect.py
python inject_site.py /tmp/site_collect.json

python worker_internshala.py is-w1
python worker_wellfound.py wf-w1
python worker_yc.py w1
python worker_linkedin.py li-w1
python worker_external.py w1
```

When a worker logs `needs-agent`, `no-apply-modal`, `fill-err`, or `submit-unconfirmed` — **stop scripting and run the agent loop.** Do not add another `button.css-abc123`.

---

## 5. Sessions

Warm profiles stay on disk (gitignored):

| Portal | Session |
|---|---|
| LinkedIn | `li_state.json` / `profiles/li_login` |
| Wellfound | `portal_wellfound.json` |
| Internshala | `profiles/is_login` |
| Himalayas | `profiles/hima_cap` |
| YC | WAAS SSO profile |

Naukri is parked (Akamai from datacenter IPs).

### Discovery resources

The following resources were added to `resource_sources.json`. They are *not* active job-board collectors: they are directories/workbooks. A discovery pass must extract current career-page URLs, validate live openings and hiring geography, then send those jobs through the normal title/JD/location/deduplication gates.

| Resource | Priority | Purpose |
|---|---:|---|
| India Remote Startups Database | High | India-focused startup and careers discovery |
| Remote Job Seeker's Resource Hub | High | Remote boards and remote-first company discovery |
| Remotive 900+ remote startups | Medium | Worldwide startup/company discovery |
| Lets Code 100% remote hiring companies | Medium | Remote company/careers discovery |
| Remotive 150+ remote startups | Medium | Additional remote startup/company discovery |

---

## 6. Truth table

`applications` is source of truth, not `jobs.status='done'`.

| Result | Meaning |
|---|---|
| audit `submitted` | Real apply |
| `needs-agent` | UI drift — agent must learn |
| `citizens-only` / `sponsorship-block` | Honest skip |
| `no-easy-apply` | LinkedIn external ATS |
| `job-expired` | Dead listing |
| `wwr-upsell-not-apply` | Career-services page, not an apply |

---

## Portals

| Site | Worker | Agent owns |
|---|---|---|
| Internshala | `worker_internshala.py` | form steps + daily cap |
| Wellfound | `worker_wellfound.py` | consent vs apply dialog |
| YC | `worker_yc.py` | company → job expand, message modal |
| LinkedIn | `worker_linkedin.py` | Easy Apply only |
| Himalayas / WWR | `worker_external.py` | expired cards, ATS vs upsell |
| Naukri | — | parked |

---

## Docs (feed these to any agent)

1. **[docs/AGENT_INSTRUCTIONS.md](docs/AGENT_INSTRUCTIONS.md)** — paste this first  
2. **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — every component  
3. **[docs/AGENT_LOOP.md](docs/AGENT_LOOP.md)** — UI repair tick  
4. GitNexus: `node .gitnexus/run.cjs analyze` then `query` / `impact` / `context`

Optional bounded UI picker: set `GROQ_API_KEY`. `dynamic_ui` sends only a
privacy-sanitized candidate inventory to the fixed Groq HTTPS endpoint and
accepts only a known candidate ID or `none`. It is never used for answers or
submit/send decisions. `UI_LLM=0` disables it. Arbitrary endpoint overrides
are intentionally unsupported so a bearer key cannot be redirected.

---

## License

MIT. Use only on accounts you own. Site ToS apply.
