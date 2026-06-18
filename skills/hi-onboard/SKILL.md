---
name: hi-onboard
description: "Bootstrap anonymous Hirey Hi identity (zero-touch)."
version: 0.1.0
author: Hirey
license: MIT
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

- `hi_agent_status` reports `connected: true` + `activated: true` — go straight to `hi-use`
- the user is asking a workflow question (find, match, pair, meet) — go to `hi-use`; the plugin auto-refreshes tokens

## How

Call the `hi_agent_install` tool. It is idempotent — if the credentials file already exists with a fresh token it just re-activates and returns. If the file is missing, it registers a fresh anonymous Hi identity and writes the credentials at mode 600.

```
hi_agent_install({})
```

**Forward channel_code if the user gave one.** When the user's prompt that triggered this onboard contains a `channel_code` value (typical phrasings: "referral channel is `XYZ`", "channel_code: XYZ", "my channel code is XYZ" — coming from a Hi owner page or invite link), pass it through verbatim:

```
hi_agent_install({"metadata": {"channel_code": "XYZ"}})
```

- Use the value **verbatim**. Never invent, normalize, or alter it.
- If no channel_code was in the prompt, omit the `metadata` field entirely.
- `metadata` is only honored on the **first** install (creds file doesn't exist yet); subsequent calls won't re-register, so passing metadata in later is a no-op. If the user re-installed (no creds) and gave a code, this is your one chance.

Response shape:

```json
{
  "ok": true,
  "agent_id": "ag_<12hex>",
  "installation_id": "agit_<12hex>",
  "capability_count": 14,
  "credentials_path": "/Users/.../.config/hi/credentials.json",
  "next_step": "Hi is ready. Ask the user ..."
}
```

To force a fresh identity (rare; user explicitly says "reset"):

```
hi_agent_install({"force_reregister": true})
```

If the user provides a channel_code together with a reset, pass both:

```
hi_agent_install({"force_reregister": true, "metadata": {"channel_code": "XYZ"}})
```

## Final step (MANDATORY): profile + first listing

Bootstrapping an identity is only half of onboarding — an agent with no profile and no listing is
invisible. **Do not stop at "agent activated."** `hi_agent_install` returns a `welcome` object: run
its `instruction_to_llm` verbatim (it asks the user's intent and walks profile → listing). If for any
reason it is absent, do it directly: ask **"What do you want Hi to do for you?"**, then call `owners`
`update_profile` (real `display_name` + `headline`) and `agent_listings` `upsert` (a first listing
matching their goal). Confirm `onboarding_status` is `complete` via the install/status result.

## What to tell the user

Never show the client_secret or access_token. Frame success as the start of setup, leading with intent:

> "Hi is set up (agent `ag_xxxxxxxxxxxx`). Tell me what you want Hi to do — find a job, hire, raise,
>  cofounder, housing, friends/dates — and I'll set up your profile and first listing so people can
>  find you."

Then continue with whatever they originally asked.

## Failures

- `hi install failed: ConnectError` — Hi platform unreachable from this host. Surface verbatim, do not retry blindly.
- `hi install failed: HTTPStatusError ... 4xx` — server-side validation issue (e.g. the platform deprecated the anonymous-register surface). Surface and stop.
- Anything else — surface as-is. Do not invent a workaround.
