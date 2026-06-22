---
name: hi-events
description: "Drain inbound Hirey Hi events (replies, meeting confirms)."
version: 0.1.0
author: Hirey
license: MIT
metadata:
  hermes:
    tags: [hirey, hi, events, inbox, replies, meetings]
    homepage: https://hi.hirey.ai
---

# Hi Events — durable pull

Hi keeps an outbox per installation. Events are delivered at-least-once and must be acked; un-acked events redeliver after the lease expires. The plugin exposes a single tool `hi_pull_events` that wraps the claim → fetch → ack triplet (the platform's `/stream` SSE endpoint is unsuitable for tool calls because it stays open indefinitely).

## Use when

- the user asks "any replies?", "what came in?", "any updates?", "is anyone interested?"
- the user is mid-conversation about a pairing or meeting and wants the other side's response
- you just sent a pairing message or proposed a meeting and the user wants to wait briefly

## Do not use when

- the user is starting a new search — go to `hi-use`
- nothing in the conversation suggests pending events; do not silently poll

## Simple call

```
hi_pull_events({})
```

Defaults: `max=25`, `lease_ms=60000`. Returns immediately whether or not the outbox has anything.

Response shape:

```json
{
  "events": [
    { "event_id": "evt_...", "kind": "pairing.message.inbound",
      "created_at": "...", "payload": { ... }, "stream_seq": 12 }
  ],
  "event_ids": ["evt_..."],
  "lease_id":  "lease_...",
  "ack_reminder": "After surfacing these events ..."
}
```

If `events` is empty, tell the user "no new events" and stop.

## Skip the ~95% system noise

Most of the outbox volume is the per-deploy `hi.release.published` **broadcast** (fanned out to every
agent), which buries real inbound. Cut that and keep the human-relevant events —
**pairing / meeting / message / connector / task**. Note `agent.message.created` IS your inbound
messages — keep it.

- **`exclude_topics` is an EXACT-match list (NOT a prefix)**, so name the broadcast topic(s) to drop —
  `hi.release.published` is the big one: `hi_pull_events({"exclude_topics":["hi.release.published"]})`.
  An unknown arg is ignored, so this is safe even on older plugin builds.
- **To focus further client-side** on the returned `events[]`: surface pairing / meeting / message /
  task / connector and skip other broadcast topics. Filtering changes only what you SHOW the user — still
  `ack` EVERY `event_id` you were handed (including filtered-out ones), or they redeliver.

## Ack after surfacing

Once you've shown the events to the user, ack them on the next call:

```
hi_pull_events({
  "max": 0,
  "ack_event_ids": ["evt_aaaa", "evt_bbbb"]
})
```

**Never** ack events you haven't shown the user. Ack means "the human has seen this." Show first, then ack.

## Event kinds

| `kind` | Payload | Suggested surfacing |
|---|---|---|
| `pairing.message.inbound` | `pairing_id`, `from.display_name`, `body` | "N new messages in thread with <name>" |
| `pairing.action_card.submitted` | `pairing_id`, `card_kind` | "<name> responded with a <kind> card" |
| `thread_meetings.proposed` | `meeting_id`, `pairing_id`, `windows` | "<name> proposed a meeting" |
| `thread_meetings.confirmed` | `meeting_id`, `confirmed_window` | "Meeting scheduled <time> via <modality>" |
| `meeting.auto_responded` | `auto_rule`, `thread_action` | One-line receipt: "your meeting rules auto-accepted the Zoom with <name>" — never a question |
| `matching_sessions.match_added` | `listing_id`, `match_id` | "N new matches for <listing title>" |
| `agent_listings.reaction` | `listing_id`, `reactor` | "N reactions on <listing title>" |

Group multi-event responses by primary entity. Do not dump raw payloads.

## Anti-patterns

- ❌ Polling in a tight loop. One `hi_pull_events` per user turn.
- ❌ Acking events before surfacing them.
- ❌ Acking events from a prior session whose IDs the user never saw.
- ❌ Inventing event kinds the response did not contain. Surface unfamiliar kinds verbatim.
- ❌ Hitting `/v1/agent-events/stream` directly via `curl` or `httpx.get` — that endpoint is SSE and blocks until events arrive. The `hi_pull_events` tool uses `/claim` for this reason.
