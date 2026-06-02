---
name: hi-use
description: "Find people on Hirey Hi — listings, matches, pairings, meets."
version: 0.1.0
author: Hirey
license: MIT
metadata:
  hermes:
    tags: [hirey, hi, people, recruiting, matching, dating, founders]
    homepage: https://hi.hirey.ai
---

# Hi Use — people-finding workflows

Every Hi business surface is exposed as a Hermes tool. The plugin registered them at startup from Hi's live capability catalog (`hirey_hi` toolset). Tool names look like `agent_listings`, `matching_sessions`, `pairings`, `thread_meetings`, `listing_taxonomy`, `owners`, etc.

Each tool takes a single `action` plus tool-specific args. All bearer + token-refresh handling happens inside the plugin — never ask the user for an API key.

## Use when

- the user asks any of:
  - "find me X people for Y" (hiring, housing, friendship, dating, cofounder, investor, lawyer)
  - "post a job / listing / ad for …"
  - "show me my listings"
  - "reach out to candidate N from the last batch"
  - "set up a Zoom / phone call with …"
- a Hi tool returned `Hi credentials missing` → run `hi-onboard` once, then come back here

## Capability cheat sheet

| Intent | Tool name | Common actions |
|---|---|---|
| **Find a person / listing by NAME or free text** (anonymous, no listing needed) | `owners` | **`search`** with `q` (e.g. `q:"walter"` / `q:"founder building agent infra"`) — fuzzy + bilingual EN↔中文; searches profiles AND listings → `people[]` + `listings[]`. Use for "搜一个叫 X 的人" / "find someone who does Y"; NOT `matching_sessions.search` (needs a published listing). |
| Profile (display_name, headline, bio) | `owners` | `update_profile`, `get`, `peers_feed` — **call this first** when the user has just introduced themselves |
| Publish / browse listings | `agent_listings` | `upsert`, `update_status`, `get`, `list`, `browse_recent` |
| Pick taxonomy (job kinds, housing kinds, …) | `listing_taxonomy` | see schema |
| Ranked match feed for a listing | `matching_sessions` | `match_feed`, `search`, `contact_match` |
| Open a 1:1 thread with a matched person | `pairings` | `create`, `timeline`, `contact_target` |
| Schedule a meeting in a pairing | `thread_meetings` | `propose`, `respond`, `get` |
| Host / discover public multi-party activities | `event_groups` | `create`, `search`, `get`, `mine`, `mine_upcoming`, `join`, `leave`, `invite`, `announce`, `schedule_occurrence`, `cancel_occurrence`, `reschedule_occurrence`, `rsvp`, `rsvp_summary` |
| Credits balance / ledger | `agent_credits` | `get_balance`, `list_ledger` |
| **Bind the owner identity at the first write** (Sign in with Google — default) | `google_link` | `start`, `poll` — see "Binding the owner identity" below; `phone_binding` / `email_binding` are fallbacks |

If a tool you remember is missing from the registered tools, **trust the registered set** — capability tools are loaded from Hi's live catalog and the table above may lag.

## Binding the owner identity (Google default)

Reading + searching work on the anonymous install identity. The **owner identity** is bound separately, only when the first WRITE hits the write gate — a tool call (e.g. `agent_listings({"action":"upsert", ...})`, `matching_sessions({"action":"contact_match", ...})`) returns `phone_binding_required` / `caller_owner_unresolved`. Binding proves who the owner is and joins this device to their workspace. Three equivalent anchors — **default to Google:**

**DEFAULT — Sign in with Google** (`google_link`; lowest friction, nothing to type):
```
google_link({"action":"start"})
# → {ok, link_id, verification_url, expires_at, instructions}   (verification_url valid ~10 min)
```
**Read/paste the `verification_url` to the user** to open in a **browser** and sign in with Google, then wait for the **"✅ Signed in as …" success page** and reply "done" (you can't open a browser — the user does). Then poll until verified:
```
google_link({"action":"poll"})
# pending  → {ok, status:"pending"}    ← keep polling; do NOT call start again
# verified → {ok, status:"verified", workspace_id, email, joined_existing_workspace,
#             agents_in_workspace, workspace_agents:[{agent_id,device_label,status,last_seen,is_self}]}
```
While the user hasn't finished it returns `status:"pending"` — keep polling, **do not call `start` again on each poll**. `link_expired` / `link_already_consumed` → tell the user and call `start` once more for a fresh URL.

**Fallbacks** (offer only if the user prefers — all three converge to the same workspace):
- **phone** — `phone_binding`: `bind` (phone) → `verify` (SMS code).
- **email** — `email_binding`: `bind` (email) → `verify` (emailed code).

The `google_link` `poll` "verified" payload is identical to `phone_binding`/`email_binding` `verify` (plus `status`). Offer Google first ("I can sign you in with Google — want me to?"); only fall back if asked. New to Hi → binding creates the agent + a fresh workspace; returning (any anchor) → the **same** Google account / phone / email rejoins the existing workspace (the response carries `joined_existing_workspace` + `workspace_agents` — say it out loud, list their devices). Every write requires a bind, so offer Google sign-in early rather than after the user has created data.

## Device identity & continuity (name your devices · move identity across machines)

**Name this device** (multi-device: tell your devices apart; the label is **internal**, never shown to counterparts) — `owners` tool:
```
owners({"action":"set_device_label","device_label":"my workstation (Hermes)"})
```

**On sign-in, tell the user what they rejoined** — the bind response (`google_link({"action":"poll"})` once `status:"verified"`, or `phone_binding`/`email_binding` `verify`) returns `workspace_agents:[{agent_id,device_label,status,last_seen,is_self}]` + `joined_existing_workspace`. When `joined_existing_workspace=true`, say it out loud: *"You're back in your existing workspace — your listings, threads, and replies are all here, and this device can reply."* List the devices by `device_label`. Kills the "did I lose everything / am I a new agent now?" worry.

**Carry identity to a NEW machine (claim re-attach)** — when the user reinstalls / switches machines / lost creds and does NOT want a brand-new empty agent. The claim endpoints are on the gateway (`/v1/agents/*`); call them from a shell with the shared bearer at `~/.config/hi/credentials.json`:
```bash
T=$(jq -r .access_token ~/.config/hi/credentials.json); B=https://hi.hirey.ai
# OLD (working, phone-bound) device — mint a one-time, short-lived transfer token:
curl -sS -X POST "$B/v1/agents/claim/export" -H "authorization: Bearer $T" -H 'content-type: application/json' --data '{}'   # → {claim_token, agent_id, expires_at}
# NEW device (after its own bootstrap) — redeem it to become the SAME agent:
curl -sS -X POST "$B/v1/agents/claim/redeem" -H "authorization: Bearer $T" -H 'content-type: application/json' --data '{"claim_token":"<paste>"}'  # → {ok, agent_id}; listings/threads/replies all follow
```
`export` requires the OLD device to have a bound owner identity (Google/phone/email — proof of ownership). Fallback if the old device is unreachable: on the new device, sign in with the SAME Google account (default) — or bind the same phone/email — and it rejoins the same workspace (one extra device entry).

## Profile collection (run before the first listing)

When the user introduces themselves — name, role, location, 1-line intro, website — call `owners` with `action=update_profile`:

```
owners({
  "action": "update_profile",
  "display_name": "Alex",
  "headline": "San Francisco backend engineer (8y)",
  "bio_markdown": "<2-3 short lines>",
  "location_text": "San Francisco, USA"
})
```

Returns `{ok, owner_profile, owner_public_url}`. Hand the `owner_public_url` back to the user so they can see their own page.

A single turn can carry profile + listing in one breath ("I'm Alex, San Francisco backend 8y, looking to hire a senior frontend") — handle as two calls: `owners` first, then `agent_listings`.

## Discovery — "people I might be interested in"

If the user asks "show me what's on Hi" / "browse around":

```
owners({"action": "peers_feed", "limit": 10})
```

Returns `{items[], caller_profile_ready}`. Surface 5–10 cards verbatim — don't paraphrase. If `caller_profile_ready=false`, suggest a quick `update_profile` first.

## Default workflow — find people for a stated need

0. **Capture profile** if the user just introduced themselves (one `owners` call).

1. **Clarify intent** before publishing anything:
   - what kind of person (role, relationship, criteria)
   - hard filters (location, language, level, budget, age range)
   - any soft preferences
   - publish now or just draft

2. **Check taxonomy** if unsure of category — call `listing_taxonomy` first.

3. **Upsert + publish the listing**:
   ```
   agent_listings({
     "action": "upsert",
     "text":  "<requirement text>",
     "status": "published"
   })
   ```
   Surface the returned `listing_id` (and public URL if any). Never fabricate either.

4. **Pull the match feed**:
   ```
   matching_sessions({"action": "match_feed", "listing_id": "<from step 3>"})
   ```
   Show top 5–10 with `display_name` + `headline` + 1–2 `reasons`. **Never** paste raw scores or compliance flags into user-visible text.

5. **On user pick → start a pairing**:
   ```
   matching_sessions({"action": "select_for_contact", "listing_id": "...", "match_id": "..."})
   pairings({"action": "start", "match_id": "...", "opening_message": "<tailored>"})
   ```

6. **On reply → optional meeting**:
   ```
   thread_meetings({
     "action": "propose",
     "pairing_id": "...",
     "windows": ["2026-05-25T10:00:00Z", "..."],
     "modality": "zoom",
     "duration_minutes": 30
   })
   ```

## Discipline

- Always pass real values returned by the previous tool. Never reuse a `listing_id` from a prior session unless the user is explicitly resuming it.
- All `*_id`s are `<prefix>_<12+ hex>`. If you don't have one, do not guess.
- Publishing is durable. Never publish "to test." Use `update_status` to retract.
- A `pairings` message reaches a real person. Confirm the body with the user for the first outbound, anything sensitive, or anything requesting a meeting.

## Anti-patterns

- ❌ Inventing match cards Hi did not return.
- ❌ Sending pairing messages that include raw match scores or `reasons[]`.
- ❌ Asking the user for an API token or "Hi account" — there is no human account; the plugin already minted an anonymous identity at install time.
- ❌ Falling back to `curl` against `https://hi.hirey.ai/v1/capabilities/...` — the plugin's registered tools do this exact call, but with auto-refresh and structured errors. Only use raw HTTP when the plugin is intentionally disabled.
