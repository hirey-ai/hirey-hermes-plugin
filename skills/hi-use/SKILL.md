---
name: hi-use
description: Use Hirey Hi for Person, Workspace, Need, Listing, People, Pairing, Message and Meeting workflows through workspace_workflows.
version: 0.2.4
---

# Use Hirey Hi from Hermes

The Hermes plugin registers one canonical business tool, `workspace_workflows`, from Hi's live
capability catalog. Its `catalog` action is authoritative. Do not look for retired tools such as
`owners`, `agent_listings`, `matching_sessions`, `pairings`, or `thread_meetings`.

Before the first business call, call `hi_agent_status`. If `plugin.update_required=true`, run the
returned update command, fully restart Hermes, and stop the current session. A recommended update
does not block a compatible request.

## Call discipline

- Call `workspace_workflows({"action":"catalog"})` before using an operation not inspected in this
  session.
- Put business inputs under `payload`. Never provide Account, Person, Workspace, Agent, or Agent
  Session authority fields.
- Every write or external effect requires a stable `idempotency_key`, reused only for its exact
  retry.
- If catalog requires explicit confirmation, ask the user first and pass
  `confirmation: {"approved":true,"operation":"<exact action>"}`.
- Reuse only identifiers returned by a preceding call. Never guess identifiers or results.
- On 401, refresh the existing credential once. On 403, follow the returned binding/scope action;
  never create a replacement Agent to bypass it.

A pending Agent may use only `people.find`, `people.explain`, and staged `capture.record`.
Private Workspace reads, contact, messaging, meetings, and publication require verified identity.

Common operation families include `person.*`, `need.*`, `listing.*`, `people.find`, `match.*`,
`pairing.*`, `message.*`, `reach.*`, `meeting.*`, and `meeting_link.*`. These are Core operation
names passed as `action`, not separate Hermes tools. If an action is absent from catalog, do not
call it.
