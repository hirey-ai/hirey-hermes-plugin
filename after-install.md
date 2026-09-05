# Hirey Hi is installed

> ⚠️ **READ THIS IF YOU INSTALLED FROM INSIDE A HERMES SESSION**
>
> Hermes builds the plugin tool registry **once per process at startup**. The TUI session you're in right now has a tool snapshot from before this plugin existed — it **cannot see the new `hi_*` tools**, no matter how many times you `/reset` or `hermes gateway restart`.
>
> **You must exit and relaunch:**
>
> ```
> /quit            # or Ctrl+D
> hermes           # relaunch
> ```
>
> Then `hi_agent_install`, `hi_agent_status`, and the canonical `workspace_workflows` tool become available. Asking "set up hi" or "find me people on Hi" will route through them.
>
> Why this matters: until you restart, the LLM will fall back to running raw `curl` + `python3` against `https://hi.hirey.ai/v1/capabilities/...` — it works but it's ~10× slower and bypasses the plugin's token refresh + structured errors.
>
> This is a known Hermes upstream limitation — see [issue #15626](https://github.com/NousResearch/hermes-agent/issues/15626) for an open feature request to fix it.

---

## What you got

New tools in the `hirey_hi` toolset:

| Tool | What it does |
|---|---|
| `hi_agent_status` | Check whether Hi credentials are valid + how many capability tools loaded |
| `hi_agent_install` | Bootstrap an anonymous Hi identity (zero user input) |
| `hi_pull_events` | Claim + fetch inbound Hi events (pairing replies, meeting confirms, match updates) |
| `hi_push_install` / `hi_push_status` / `hi_push_remove` | Opt-in push delivery: Hi cloud POSTs events to your Hermes gateway instead of you polling |
| `workspace_workflows` | One canonical business tool; call its live `catalog` action first |

And four skills (auto-loaded in `<available_skills>`):
- `hi-onboard` — first-time setup
- `hi-use` — Person, Workspace, listings, matching, pairings and meetings
- `hi-events` — canonical business Inbox
- `hi-repair` — Product Signal and bounded repair workflow

## First-time setup (one tool call)

In a **fresh** Hermes session, just say:

> "set up hi"

…or directly:

```
/hi-onboard
```

That calls `hi_agent_install` which registers an anonymous Hi identity at `~/.config/hi/credentials.json` (mode 600). No Hi account, no browser, no human input.

## Then ask people-shaped questions

> "find me 10 backend engineers in San Francisco"
> "post a listing for a cofounder in fintech, equity-only"
> "reach out to the top 3 from yesterday's matches"
> "schedule a 30-min Zoom with Alex next Wednesday"
> "any replies?"

## Optional: enable push delivery

Pull is the default. To have Hi cloud POST inbound events directly to your gateway:

```
hi_push_install({"public_url": "https://your-public-hostname/webhooks/hi"})
```

If you're on a laptop behind NAT, set up an ngrok / cloudflared tunnel first. Loopback URLs are refused by default because Hi cloud cannot reach them — pass `force_register_with_loopback: true` if you just want to exercise the registration path.

## Privacy

- Installation starts with a pending Agent and no Person. Anonymous public reads work immediately; Google, email, or phone verification is requested only for private Workspace data and writes, and binds the existing Agent rather than creating another one.
- Credentials live at `~/.config/hi/credentials.json` (mode 600, dir mode 700).
- All Hi traffic is HTTPS to `https://hi.hirey.ai/v1/*`.
- The same credentials file is shared with Hirey's Claude Code plugin if you have both installed — one Hi identity across both hosts.
