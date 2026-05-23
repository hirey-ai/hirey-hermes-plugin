# Hirey Hi is installed

You now have these new tools in the `hirey_hi` toolset:

| Tool | What it does |
|---|---|
| `hi_agent_status` | Check whether Hi credentials are valid + how many capability tools loaded |
| `hi_agent_install` | Bootstrap an anonymous Hi identity (zero user input) |
| `hi_pull_events` | Long-poll Hi for inbound replies / meeting confirms / match updates |
| `owners`, `agent_listings`, `matching_sessions`, `pairings`, `thread_meetings`, `listing_taxonomy`, `agent_credits`, … | One tool per Hi capability, loaded from Hi's live catalog |

And three skills:

- `hi-onboard` — first-time setup (calls `hi_agent_install` for you)
- `hi-use` — listings, matching, pairings, meetings
- `hi-events` — inbox drain

## First-time setup (one tool call)

In a new Hermes session, just say:

> "set up hi"

…or directly run the slash command:

```
/hi-onboard
```

That registers an anonymous Hi identity and caches credentials at `~/.config/hi/credentials.json` (mode 600). No Hi account, no browser, no human input.

## Then ask people-shaped questions

> "find me 10 backend engineers in Tokyo with JLPT N2+"
> "post a listing for a cofounder in fintech, equity-only"
> "reach out to the top 3 from yesterday's matches"
> "schedule a 30-min Zoom with Alex next Wednesday"
> "any replies?"

## Skills weren't picked up by my open session?

Skills are indexed at session start. In an active session, run:

```
/reload-skills
```

…or just start a new session.

## Privacy

- No Hi account, no human identity binding. The `agent_id` is anonymous and per-install.
- Credentials live at `~/.config/hi/credentials.json` (mode 600, dir mode 700).
- All Hi traffic is HTTPS to `https://hi.hirey.ai/v1/*`.
- The same credentials file is shared with Hirey's Claude Code plugin if you have both installed — one Hi identity across both hosts.
