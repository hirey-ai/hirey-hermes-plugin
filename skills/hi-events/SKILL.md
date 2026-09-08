---
name: hi-events
description: Read and process the current Person's Hirey Hi business inbox through workspace_workflows.
version: 0.2.4
---

# Hi business inbox from Hermes

Use `workspace_workflows`; `hi_pull_events` is transport plumbing and is not the user's business
Inbox.

1. For messages, replies, tasks, notifications, or work needing attention, first call
   `workspace_workflows` with `action:"agent_message.list"`.
   Do not answer from session history, transport logs, cached prose, or a remembered web URL.
2. Read `result.items`. Listing is read-only; do not claim, mark, acknowledge, or complete an item
   merely to inspect it.
3. Call `agent_message.claim` only for an `agent_request` that must actually be processed. Show its
   human-relevant content before completing it.
4. Complete or fail only the exact lease returned by the claim. Other items use their actions from
   the live catalog.

Core hides transport-only pending, leased, retry, delivery-attempt, and dead-letter states. Never
describe them as user messages. A zero-item response means nothing user-visible matched this
request; it is not evidence about another Person or Workspace.

If the call returns `verified_login_required`, say that the inbox was **not checked** and that its
contents are still unknown. Offer `google_link` first, with email or phone binding only as
fallbacks. Never turn an authorization failure into "no messages".

Do not offer a web inbox as a substitute for the tool call and never invent or reuse a remembered
Hi URL. In particular, `https://hi.hirey.ai/inbox` is a retired legacy route. If the user asks to
open Hirey in a browser, use only a current URL returned by the live operation or the canonical
human app at `https://hirey.ai/me`.
