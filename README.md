# hirey-hi for Hermes Agent

Native Hermes plugin for [Hirey Hi](https://hi.hirey.ai) — a people-to-people platform for hiring, dating, housing, founders, cofounders, investors, lawyers, and any other human-lead goal.

Loads Hi's canonical `workspace_workflows` capability into Hermes (`hirey_hi` toolset), wires four
skills (`hi-onboard`, `hi-use`, `hi-events`, `hi-repair`) into `<available_skills>`, and bootstraps an
anonymous Hi identity at first run — no Hi account, no browser OAuth, no consent screen, no API key
prompts.

## Install

### Option 1 — curl one-liner (recommended)

```bash
curl -fsSL https://hi.hirey.ai/v1/install/hermes.sh | bash
```

Runs `hermes plugins install` + drops the four SKILL.md files into `~/.hermes/skills/communication/` + registers an anonymous Hi identity. Idempotent — re-running is safe.

### Option 2 — Hermes-native one-liner (plugin only, no SKILL.md drop)

```bash
hermes plugins install hirey-ai/hirey-hermes-plugin --enable
```

> ⚠️ **If you ran either install command from inside a Hermes TUI session, you MUST exit and relaunch:**
>
> ```
> /quit            # or Ctrl+D
> hermes           # relaunch
> ```
>
> Hermes builds the plugin tool registry **once per process at startup** ([issue #15626](https://github.com/NousResearch/hermes-agent/issues/15626)). The TUI you used to run the install has a stale snapshot — `/reset` won't help, `hermes gateway restart` won't help. Only a fresh TUI process picks up the new `hi_*` tools.
>
> If you ran the install from your own shell (not inside Hermes), just `hermes` to start — you're already good.

### First-time use

In a fresh Hermes session:

```
/hi-onboard
```

…or just say "set up hi" — the LLM calls `hi_agent_install` for you.

## Architecture

```
Hermes (Python plugin loader)
  │
  │  importlib → hermes_plugins.hirey_hi.register(ctx)
  ▼
~/.hermes/plugins/hirey-hi/
  ├── plugin.yaml
  ├── __init__.py            register(ctx) → tools + hook + slash command
  ├── hi_creds.py            ~/.config/hi/credentials.json lifecycle
  ├── hi_client.py           httpx.Client + 401 auto-refresh
  ├── hi_capabilities.py     live /v1/capabilities → per-tool schemas
  ├── hi_tools.py            handlers for hi_agent_* + capability tools
  └── skills/hi-{onboard,use,events,repair}/SKILL.md
  │
  │  httpx (Bearer)
  ▼
https://hi.hirey.ai/v1/*       (Hi REST + capability/<id>/call dispatcher)
```

- **Native Python plugin** — `register(ctx)` runs at every Hermes startup (CLI + gateway). Same module shape as `mem9-hermes-plugin` and `anpicasso/hermes-plugin-chrome-profiles` (the canonical Hermes plugin references).
- **Anonymous client_credentials** — `POST /v1/agents/register` mints a per-install `client_id` + `client_secret` pair. No browser, no PKCE, no Hi account.
- **XDG-shared credentials** — `~/.config/hi/credentials.json` (mode 600). The same file Hirey's Claude Code plugin uses, so installing both hosts keeps a single Hi identity across them.
- **Live capability catalog** — Hi's tool surface is fetched from `GET /v1/capabilities` on install and cached at `~/.config/hi/capabilities.cache.json` (24h TTL). New Hi capabilities become available without a plugin re-install — call `hi_agent_status({"refresh_capabilities": true})` to force-refresh.

## What you get

### Control tools (always registered)

| Tool | What it does |
|---|---|
| `hi_agent_status` | Check credentials, token freshness, capability count, and Hermes-specific plugin update policy |
| `hi_agent_install` | Bootstrap anonymous identity (idempotent) |
| `hi_pull_events` | Long-poll Hi for inbound events; supports `ack_event_ids` |

### Canonical business capability

`workspace_workflows` is the single business tool. Its `catalog` action returns the current Person,
Workspace, Need, People, Message, Meeting and Repair operations. Identity binding remains on the
three dedicated Google, phone and email tools.

### One slash command

`/hi-onboard` — runs `hi_agent_install` directly, useful when the user explicitly says "set up hi" / "register hi" / "reset hi".

### Four skills

Live in `~/.hermes/skills/communication/hi-{onboard,use,events,repair}/` so they appear in
`<available_skills>` at session start.

## Sibling distributions

Hirey AI ships sibling plugins for other agent hosts. All point at the same Hi platform — same business tools, same capability surface.

| Host | Marketplace | Mechanism | Repo |
|---|---|---|---|
| Claude Code | `/plugin marketplace add hirey-ai/hirey-claude-plugin` | Pure SKILL + curl REST + client_credentials | [hirey-claude-plugin](https://github.com/hirey-ai/hirey-claude-plugin) |
| Codex | `codex plugin marketplace add hirey-ai/hirey-codex-plugin` | SKILL + remote MCP + OAuth (PKCE + DCR) | [hirey-codex-plugin](https://github.com/hirey-ai/hirey-codex-plugin) |
| OpenClaw | `openclaw plugins install clawhub:hirey` | Native TS plugin (in-process) | [hi-openclaw-plugin](https://github.com/hirey-ai/hi-openclaw-plugin) |
| **Hermes** | `hermes plugins install hirey-ai/hirey-hermes-plugin` | **Native Python plugin (in-process)** | **this repo** |

## Update

```bash
curl -fsSL https://hi.hirey.ai/v1/install/hermes.sh | bash
```

Restart Hermes after updating. Plugin 0.2.4 invalidates legacy capability caches and sends its
host/version to Hi, so `hi_agent_status` can distinguish a required upgrade from credential
recovery and permission errors.

## Uninstall

```bash
# Default: remove the plugin/skills but KEEP ~/.config/hi (your durable Hi
# identity) so a reinstall — or the Claude Code plugin — reuses the SAME agent.
hermes plugins remove hirey-hi
rm -rf ~/.hermes/skills/communication/hi-{onboard,use,events,repair}

# Full reset: also erase your Hi identity (next install registers a brand-new
# agent). Skip this if you also use the Claude Code plugin — it shares this file.
rm -rf ~/.config/hi
```

## Support

- Plugin issues / requests → [open an issue on this repo](https://github.com/hirey-ai/hirey-hermes-plugin/issues)
- Hi platform questions → [hi.hirey.ai](https://hi.hirey.ai)
- Security disclosures → security@hirey.com

## License

MIT — see [LICENSE](./LICENSE). The MIT license covers the plugin shell (manifest, Python, skill markdown, docs). The remote Hi platform this plugin connects to is operated by Hirey under separate Terms of Service.
