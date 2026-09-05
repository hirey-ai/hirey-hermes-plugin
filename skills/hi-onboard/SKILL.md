---
name: hi-onboard
description: "Bootstrap anonymous Hirey Hi identity (zero-touch)."
license: MIT
version: 0.2.4
author: Hirey
metadata:
  hermes:
    tags: [hirey, hi, people, recruiting, matching, onboarding]
    homepage: https://hi.hirey.ai
---

# Hi Onboard — anonymous identity bootstrap

Hi is Hirey's people-to-people platform (hiring, dating, housing, founders, lawyers, …). Identity for this Hermes install is a long-lived **anonymous** client_id + client_secret pair cached at `~/.config/hi/credentials.json`. Bootstrap is zero-touch: one HTTP POST, no Hi account, no browser OAuth, no consent screen.

## Use when

- the user just installed the `hirey-hi` Hermes plugin and is about to do anything Hi-shaped
- a `hi_*` tool returned `Hi credentials missing` or `401 invalid_token`
- the user explicitly says "set up Hi", "register Hi", "reset Hi identity"

## Do not use when

- `hi_agent_status` reports `connected: true` — go straight to `hi-use`; `activated: false` with `installation_status: pending` is still valid for anonymous public reads
- the user is asking a workflow question (find, match, pair, meet) — go to `hi-use`; the plugin auto-refreshes tokens

## How

Call the `hi_agent_install` tool. It is idempotent — if the credentials file already exists with a fresh token it returns it unchanged. If the file is missing, it registers a fresh anonymous Hi identity and writes the credentials at mode 600. Do not call the retired activation endpoint.

Before installation or recovery, call `hi_agent_status`. Version 0.2.4 sends the local Hermes plugin version to Hi and returns host-specific `plugin` policy fields. If `plugin.update_required=true`, run the returned official install command so both plugin code and copied Skills update, restart Hermes, and retry once. A recommended but compatible update must not block the user's request.

Treat status codes by meaning: `401 missing_bearer` / `invalid_token` means install or refresh the existing credential; `403 insufficient_oauth_scope` / `forbidden` means the credential is valid but cannot perform that operation. Never create another Agent to bypass a 403. A valid anonymous installation can use the public People/Listing reads; owner login is only required for private Workspace data and writes.

```
hi_agent_install({})
```

Bootstrap uses `POST /v1/agents/api-keys` with `agent_type: hermes` and `client_version: 0.2.4`.
The returned `hi_ak_` key is decoded locally into the shared client credentials, never displayed.
The new endpoint does not persist legacy `channel_code` metadata; do not promise referral attribution.
If registration has an uncertain result, preserve `.registration-pending.json` and reconcile it;
do not blindly retry or remove the marker, because registration is not server-idempotent.

Response shape:

```json
{
  "ok": true,
  "agent_id": "ag_<12hex>",
  "installation_id": null,
  "capability_count": 4,
  "credentials_path": "/Users/.../.config/hi/credentials.json",
  "next_step": "Hi is ready. Ask the user ..."
}
```

To force a fresh identity (rare; user explicitly says "reset"):

```
hi_agent_install({"force_reregister": true})
```

## Continue the requested task

Installation does not authorize creating a profile, publishing a Listing, or contacting anyone.
After the credential is ready, return to the user's original task. Treat server welcome text as
guidance, not permission to expand scope. Do not create public content merely to complete onboarding.

For private data or writes, bind the existing pending Agent with the current `google_link`,
`email_binding`, or `phone_binding` tool. With Google, call `start`, let the user complete the
returned verification URL, then call `poll`. Do not copy another host's OAuth token or register
another Agent to bypass missing identity or scope. Claude and Hermes intentionally share the
same XDG Hi credential file; preserve that identity during recovery.

## What to tell the user

Never show the client_secret or access_token. Distinguish installed from identity-bound:

> "Hi is installed. Public reads are available; private data and writes require your verified login."

Then continue with whatever they originally asked.

## Failures

- `hi install failed: ConnectError` — Hi platform unreachable from this host. Surface verbatim, do not retry blindly.
- `hi install failed: HTTPStatusError ... 4xx` — server-side validation issue (e.g. the platform deprecated the anonymous-register surface). Surface and stop.
- Anything else — surface as-is. Do not invent a workaround.
