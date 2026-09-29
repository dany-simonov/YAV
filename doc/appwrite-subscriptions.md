# Subscription policy notes

The authoritative Appwrite TablesDB schema, indexes, permissions, Function key
scopes and deployment order are in
[appwrite-workspaces.md](appwrite-workspaces.md). This file intentionally does
not duplicate that matrix.

Canonical tiers are `free`, `pro`, `enterprise`, and `custom`. Legacy
`users.plan=premium` maps to `pro` if `users.subscription` is absent.
`quota_overrides` permits only `checks` and `heavy_media_checks`, while
`quota_usage_generations` is a migration marker for `user_quota_generations`.
Both are server-owned. System administrators are only validated IDs from
`SYSTEM_ADMIN_USER_IDS`; neither request fields nor profile values grant admin
authority.
