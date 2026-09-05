---
name: hi-repair
description: Use Product Signal and Repair Case operations through workspace_workflows.
version: 0.2.4
---

# Hi Repair from Hermes

Call `workspace_workflows({"action":"catalog"})` first and follow the live `product_signal.*` and
`repair.*` definitions.

- Reporter: `product_signal.submit`, then `product_signal.get`; verify only after a released repair
  asks the reporter to verify.
- Authorized staff: triage the signal, then create a separate case with `repair.case.create`.
- Repair worker: use a current `repair.grant.*` authority and exclusive `repair.run.claim`; maintain
  its lease, attach bounded evidence, and finish with `repair.run.finish`.
- Release operator: review and merge outside the repair worker, deploy explicitly, and use
  `repair.release.advance` only with typed evidence.

Every write needs a stable `idempotency_key`. Explicit-confirmation operations require the user's
approval and exact confirmation object. A Repair Grant never grants merge, deploy, or production
authority.
