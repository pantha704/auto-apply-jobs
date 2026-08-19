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
| **Browser Use** | Skill/CLI attached to CloakBrowser over loopback CDP | Unknown-site discovery and bounded drift recovery |
| **Playwright CLI** | `playwright-cli` + agent skills, attached over CDP | Token-efficient inspection, deterministic replay, traces, and recipe generation |
| **CloakBrowser MCP** | `npx -y cloakbrowser-mcp@latest` (Hermes `mcp_servers.cloakbrowser`) | Optional deep diagnosis on the same Cloak engine |
| **Playwright MCP** | `playwright-mcp` / Hermes `browser_*` toolset | Optional persistent a11y/repair sessions |

Workers do **not** use MCP as their normal execution path. A verified recipe runs through Playwright; Browser Use discovers unknown states; MCP is the final supervised diagnostic escalation.

## Install (operator)

1. Install **CloakBrowser non-pro** for your OS. Confirm the `chrome` binary exists.
2. Install the Python driver with `pip install -r requirements.txt` — **do not** run `playwright install chromium`.
3. `export CLOAK=/path/to/cloakbrowser/.../chrome`
4. For the target router, attach Browser Use and Playwright CLI to a loopback-only CloakBrowser CDP endpoint. Enable CloakBrowser MCP or Playwright MCP only when deep interactive diagnosis is required.

## Do not

- Point Playwright at Google Chrome / `playwright install chromium`
- Share one Cloak `user_data_dir` across collector + worker
- Login while a scrape is using the same profile
