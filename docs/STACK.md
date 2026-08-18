# Browser stack (exact)

This farm does **not** use stock Google Chrome and does **not** use
`playwright install chromium`.

## Runtime (workers + collectors)

| Piece | What it actually is |
|---|---|
| **CloakBrowser (non-pro)** | Stealth browser binary. On this host: `~/.cloakbrowser/chromium-146.0.7680.177.5/chrome` (version folder changes on upgrade). Set `CLOAK=` to that path. |
| **Playwright (Python)** | Driver only. `from playwright.sync_api import sync_playwright` then `launch_persistent_context(..., executable_path=CLOAK, ...)`. |
| **Persistent profiles** | `profiles/<name>` + gitignored `portal_*.json` / `li_state.json` |
| **TMPDIR** | Real disk (e.g. `/home/ubuntu/tmp_chrome`). tmpfs quotas kill the Cloak process. |

```python
CLOAK = os.environ.get(
    "CLOAK",
    os.path.expanduser("~/.cloakbrowser/chromium-146.0.7680.177.5/chrome"),
)
ctx = p.chromium.launch_persistent_context(
    user_data_dir=PROFILE,
    executable_path=CLOAK,   # CloakBrowser binary — not Playwright's Chromium
    headless=True,
    args=["--no-first-run", "--disable-blink-features=AutomationControlled"],
)
```

`p.chromium` here is Playwright’s **CDP protocol name**. The process that starts is CloakBrowser.

Non-pro = no Cloak Pro license features. Do not document or commit `license.key`.

## Agent side (MCP)

Both are part of the operator stack:

| MCP | How | For |
|---|---|---|
| **CloakBrowser MCP** | `npx -y cloakbrowser-mcp@latest` (Hermes `mcp_servers.cloakbrowser`) | Agent-driven browse on the same Cloak engine (navigate, snapshot, click) |
| **Playwright MCP** | `playwright-mcp` / Hermes `browser_*` toolset | A11y snapshots, repair sessions, `docs/AGENT_LOOP.md` drift work |

Workers do **not** go through MCP. MCP is how an **agent** looks at a live page when the intent map misses.

## Install (operator)

1. Install **CloakBrowser non-pro** for your OS. Confirm the `chrome` binary exists.
2. Install the Python driver with `pip install -r requirements.txt` — **do not** run `playwright install chromium`.
3. `export CLOAK=/path/to/cloakbrowser/.../chrome`
4. Enable MCP in the agent host:
   - CloakBrowser: `npx -y cloakbrowser-mcp@latest`
   - Playwright MCP as provided by your host (Hermes `browser` toolset / `playwright-mcp`)

## Do not

- Point Playwright at Google Chrome / `playwright install chromium`
- Share one Cloak `user_data_dir` across collector + worker
- Login while a scrape is using the same profile
