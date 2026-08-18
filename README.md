# auto-apply-jobs

A **multi-portal job-apply farm** meant to be **run by an AI agent**, not a pile of frozen CSS selectors.

Sites change their UI every week. Hardcoded `button.easy-apply` **will** rot. This repo’s contract is:

1. Workers try a **small, intent-based** apply (role/name/text, not one class name).
2. On mismatch they **stop and ask an agent** (screenshot + DOM + reason) instead of clicking the wrong thing.
3. The agent **patches the intent map** (or the worker) and requeues. That patch *is* the learning.

Humans set identity + sessions. Agents run, watch, and repair.

---

## How it fits together

```
 you (once)                    agent loop (ongoing)
 ┌──────────────┐              ┌─────────────────────────────┐
 │ profile_local│              │ 1 collect → inject queue    │
 │ resume.pdf   │              │ 2 workers claim + apply     │
 │ portal login │─────────────▶│ 3 audit / skip honestly     │
 └──────────────┘              │ 4 UI break? → screenshot    │
                               │ 5 agent patches intent map  │
                               │ 6 requeue + continue        │
                               └─────────────┬───────────────┘
                                             ▼
                                    apply_queue.db
                                    applications (truth)
                                    learned/selectors.json
```

**Do not treat worker `*.py` as a finished bot.** Treat them as a fast path. The README + `docs/AGENT_LOOP.md` are how a stranger (or Hermes / Claude Code / Codex / OpenCode) actually operates the farm.

---

## Requirements

| Need | Notes |
|---|---|
| Linux | headless VPS is fine |
| Python 3.11+ | venv + `pip install playwright` (**driver only**) |
| **CloakBrowser non-pro** | Binary at `~/.cloakbrowser/chromium-<ver>/chrome`. **Not** stock Chromium. |
| Playwright Python | Wires to Cloak via `executable_path=CLOAK`. Do **not** `playwright install chromium`. |
| Agent MCPs | CloakBrowser MCP (`npx -y cloakbrowser-mcp@latest`) **and** Playwright MCP (`browser_*` / `playwright-mcp`) |
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
# Agent host: enable CloakBrowser MCP AND Playwright MCP

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
> Collect → inject → run workers → if a portal UI drifted, screenshot +
> accessibility snapshot, update learned/selectors.json (intent → role/name/text),
> patch the worker only if the intent map is not enough, requeue those jobs,
> commit the learning. Never invent form answers. Never commit secrets.

That is how the farm **self-learns**: each UI break becomes a checked-in intent, not a one-off “fix it in chat”.

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

Optional cheap UI brain: set `GROQ_API_KEY`. `dynamic_ui` will ask Groq
`llama-3.1-8b-instant` (~$0.05/$0.08 per M tokens) **only** to pick a visible
control when the intent map misses. Hits are written back to
`learned/selectors.json`. Never used for form answers. `UI_LLM=0` disables it.
Any OpenAI-compatible endpoint works (`UI_LLM_BASE` + `UI_LLM_API_KEY`).

---

## License

MIT. Use only on accounts you own. Site ToS apply.
