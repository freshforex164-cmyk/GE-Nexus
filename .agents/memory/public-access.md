---
name: GE Nexus public access
description: Project-specific instruction to keep GE Nexus publicly accessible without app authentication.
---

Keep GE Nexus app and API routes unauthenticated. Do not add, restore, or modify
authentication.

**Why:** the user said the app must remain publicly accessible for Render
deployment.

**How to apply:** preserve public route behavior during future changes. The
current Render startup guard still requires `GE_NEXUS_ACCESS_PASSWORD` when
`RENDER=true`, even though it does not protect requests; keep this separate from
the user's no-authentication instruction and do not silently re-enable auth.
