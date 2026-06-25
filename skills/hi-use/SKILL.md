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
| **Get the public URL of anything you made** (pages + share links) | `public_pages` | `get` (no args = ALL your URLs; or `ref={kind,id\|public_id}` for one thing) |
| Create / manage the user's company page | `companies` | `create`, `update`, `get`, `archive`, `list_recent`, `list_listings` |
| Resolve "who is this" + public URLs from any id | `agents` | `resolve` (`by`=`owner_public_id`/`company_id`/`listing_id`/…) |
| Pick taxonomy (job kinds, housing kinds, …) | `listing_taxonomy` | see schema |
| Ranked match feed for a listing | `matching_sessions` | `match_feed`, `search`, `contact_match` |
| Open a 1:1 thread with a matched person | `pairings` | `create`, `timeline`, `contact_target`, **`contact_owner`** (reach an owner straight from `search`/`peers_feed` — no listing/match needed) |
| Schedule a meeting in a pairing | `thread_meetings` | `propose`, `respond`, `get` |
| **Standing rules to auto-accept / auto-decline meeting requests** (no per-request confirmation) | `meeting_rules` | `set`, `get`, `clear` — e.g. "founders/investors about AI agents, weekdays 10:00–18:00 PT → accept; pure sales → decline". Hi evaluates and responds **platform-side** when a request arrives, even while this host is offline; each auto action is reported via a `meeting.auto_responded` event. |
| Host / discover public multi-party activities | `event_groups` | `create`, `search`, `get`, `mine`, `mine_upcoming`, `join`, `leave`, `invite`, `announce`, `schedule_occurrence`, `cancel_occurrence`, `reschedule_occurrence`, `rsvp`, `rsvp_summary` |
| Credits balance / ledger | `agent_credits` | `balance`, `ledger` |
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

## Binding / connecting your identity to Hi (proactive)

When the user wants to **bind / connect / add / save** their **email, phone, or Google account to Hi** — to keep their identity, recover their workspace across devices/reinstalls, or unlock writes — use **Hi's OWN tools** (call shapes are in "Binding the owner identity" above; this is just the routing):

- **Email → default `google_link`** (one-click Sign in with Google — `start` → give the user the `verification_url` → `poll`). If they'd rather not use Google, **`email_binding`** (`bind` → `verify` with the emailed code).
- **Phone → `phone_binding`** (`bind` → `verify` with the SMS code).

⚠️ **This is Hi's identity binding, NOT a host-native email/Gmail/calendar connector.** Never tell the user to reauthorize or reconnect a host app (e.g. a Hermes/OpenClaw "Gmail connector") for this — that's a different thing and won't bind them to Hi. If a host shows a "reauthenticate this app" message for some Gmail/email connector, that is NOT how you bind email to Hi; call `google_link` / `email_binding` instead.

The three anchors (phone / email / Google) are **equivalent and additive in ANY order**: a user who already bound one can bind another later and it **converges to the same workspace** — never a second account. So "I bound my phone, now I also want to add my email/Google" (and vice-versa) just works — go ahead and bind the additional anchor.

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

When the user introduces themselves — name, role, location, 1-line intro, website — call `owners` with `action=update_profile`. Use their **real name** (the platform's outbound gate now rejects generic agent/device labels like "Hi agent"; the counterpart sees this name on matches and meeting invites):

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

## Public pages & share links — every published thing has a shareable URL

Everything the user creates on Hi has a public web page they can open and forward (no login to view), all cross-linked:
- **owner / personal page** — `hi.hirey.ai/owner/<id>` (also the "agent page" — same page),
- **company page** — `hi.hirey.ai/company/<id>`,
- each **listing / demand page** — `hi.hirey.ai/listing/<id>`.

**Hand the URL back after every publish** — each write returns its link:
- `agent_listings` `upsert` / `update_status` / `get` → `listing_public_url` (+ `listing_public_url_status`: `public` / `unlisted` / `private_not_shareable`; null when private or not open).
- `owners` `update_profile` / `get` → `owner_public_url`.
- `companies` `create` / `update` / `get` → `company.public_url` (+ `company.owner_public_url`).

**When the user asks "what's my page / link?" call `public_pages`** — one place for any/all URLs:
- `public_pages` `get` (no args) → `{owner_public_url, company_public_url, listings:[{listing_id, summary, status, listing_public_url, listing_public_url_status}]}`.
- `public_pages` `get` with `ref={kind,id|public_id}` → `{public_url, public_url_status}` for one thing (kind = `listing` | `owner` | `agent` | `company`).

A private or closed listing has no shareable URL (`public_url_status` says why) — say so instead of inventing a link.

## Discovery — "people I might be interested in"

If the user asks "show me what's on Hi" / "browse around":

```
owners({"action": "peers_feed", "limit": 10})
```

Returns `{items[], caller_profile_ready}`. Surface 5–10 cards verbatim — don't paraphrase. If `caller_profile_ready=false`, suggest a quick `update_profile` first.

**Reaching one of these owners — use `contact_owner`, no listing needed.** `peers_feed` / `search` return each owner's `owner_public_url`, which carries their public id. Call `pairings({"action":"contact_owner","target_owner_public_id":<that id>,"text":"…"})` (or pass `target_owner_customer_id` / `target_agent_id`) — Hi opens the thread directly; you do **not** need a listing, a match, or `contact_match` first. You must have your own owner profile set up (run `update_profile` if you hit `caller_owner_unresolved`). Reserve the listing → matching → `contact_match` flow for acting on a specific published listing.

## Find a specific person by name → search FIRST (a name in a listing ≠ that person)

When the user names someone or says "find / contact / reach **<name>** [in <place>]", your FIRST call is `owners({"action":"search","q":"<name> <place>"})` (e.g. `q:"Mark Arizona"`) — **before** any match feed. Then reach them with `pairings({"action":"contact_owner","target_owner_public_id":…})` — no listing needed.

- **A name inside a *listing's body* is NOT that person.** `matching_sessions` rank *listings*; a listing reading "looking for someone named Mark" is its **author's wanted counterpart**, not Mark — never present the listing's author/subject as the person searched for.
- **Put the place in the query** — a bare common name returns a wall of unrelated people; `"Mark Arizona"` floats the right one up.
- **Never say "there is no <name>"** until `owners({"action":"search"})` has actually run and you've reported its literal `people[]`. "Not in the match feed" ≠ "doesn't exist."

## Default workflow — find people for a stated need

0. **Set up: outline the plan, then capture the user's real identity.** For a new user, first tell them in one line how Hi works: *"I'll set up your Hi profile (your real name + a one-line headline), post what you're looking for, show you matches, and connect you — we can even schedule a Zoom, all from chat."* Then capture their profile with their **real name** (one `owners` call).

1. **Clarify intent** before publishing anything:
   - what kind of person (role, relationship, criteria)
   - hard filters (location, language, level, budget, age range)
   - any soft preferences
   - publish now or just draft

2. **Check taxonomy** if unsure of category — call `listing_taxonomy` first.

3. **Upsert the listing, then open it** (`status` is NOT accepted on `upsert` — it returns `status_not_allowed_in_upsert_use_update_status`):
   ```
   agent_listings({
     "action": "upsert",
     "text":  "<requirement text>"
   })
   // then open it (status lives on update_status, never on upsert):
   agent_listings({
     "action": "update_status",
     "listing_id": "<from upsert>",
     "status": "open"
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
