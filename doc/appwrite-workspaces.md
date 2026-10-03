# Appwrite TablesDB deployment contract

This is the single authoritative schema, index, permission and deployment
contract for the canonical Function (`src/main.py` and `src/`). It describes
code expectations, not the state of a live Appwrite project. `api/` is legacy
and excluded; this repository does not provision or mutate Appwrite resources.

All IDs validated by the Function match `[A-Za-z0-9._-]{1,36}`; deterministic
row IDs are 32/36-character UUID/hash values. All tables also have Appwrite
system fields `$id`, `$createdAt`, and `$sequence`. Cursor-paginated queries
use `$sequence`; non-paginated member and reservation views retain their
explicit `$createdAt` presentation order. No custom ordering field exists.

## Schema matrix

### `users`

| Attribute | Type / limit | Required and legacy behavior | Writer | Code |
| --- | --- | --- | --- | --- |
| `name` | string, 128 | new profiles | Function bootstrap | `ensure_user_profile` |
| `email` | string/email, 320 | new profiles; legacy empty accepted | Function mirrors Auth | `appwrite_store.py`, `admin.py` |
| `plan` | string/enum, 16 | legacy `free`/`premium` fallback | legacy/bootstrap | `subscriptions.py` |
| `subscription` | enum/string, 16 | optional pre-backfill; absent means legacy `plan` | Function admin only | `subscriptions.py` |
| `quota_overrides` | string, 1024 | optional; absent means `{}` | Function admin only | `subscriptions.py` |
| `provider_quota_overrides` | string, 1024 | optional JSON map: `gemini`, `sightengine`, `aiornot`, `sapling`, `resemble` → positive monthly limit | Function admin only | `subscriptions.py`, `rate_limit.py`, `admin.py` |
| `quota_usage_generations` | string, 1024 | optional migration marker; absence retains legacy subjects | Function admin only | `subscriptions.py` |
| `status` | string/enum, 16 | new profiles | Function bootstrap | `ensure_user_profile` |
| `email_verified` | boolean | new profiles | Function mirrors Auth | `ensure_user_profile` |
| `checks_count` | integer | default `0` | Function only | `persist_check_result` |
| `last_check_at` | datetime/string, 64, nullable | nullable | Function only | `persist_check_result` |

Existing profile owner permissions are legacy-compatible, but must not give a
client write path to policy, quota, count, or timestamp fields.

### `checks`

Every new row has `permissions: []`; personal/workspace history is only served
by Function actions.

| Attribute | Type / limit | Required | Writer | Code |
| --- | --- | --- | --- | --- |
| `user_id`, `workspace_id` | string, 36 | `user_id` yes; `workspace_id` optional/NULL for personal | Function | `map_analysis_to_check_row` |
| `media_type`, `status` | string/enum, 16 | yes | Function | `map_analysis_to_check_row` |
| `verdict` | string/enum, 24 | yes | Function | `map_analysis_to_check_row` |
| `semantics_version`, `authenticity_index`, `processing_ms` | integer | first two nullable; processing required | Function | `map_analysis_to_check_row` |
| `ai_probability`, `decision_confidence` | float, nullable | optional | Function | `map_analysis_to_check_row` |
| `provider`, `model` | string, 32 / 128, nullable | optional | Function | `map_analysis_to_check_row` |
| `explanation`, `source_label` | string, 2000 / 120, nullable | optional | Function | `map_analysis_to_check_row` |
| `details` | string, at least 16384 bytes | yes | Function | `serialize_check_details` |

### `rate_limits`

Server-only atomic counter rows; the ID is a deterministic hash of
`(dimension, subject, window_start)`.

| Attribute | Type / limit | Required | Writer | Code |
| --- | --- | --- | --- | --- |
| `dimension` | string, **64** | yes | Function transaction | `rate_limit.py` |
| `subject` | string, **64** | yes | Function transaction | `rate_limit.py` |
| `window_start` | string, 32 | yes | Function transaction | `rate_limit.py` |
| `window_end` | datetime/string, 64 | yes | Function transaction | `rate_limit.py` |
| `count` | integer | yes | Function increment/decrement | `rate_limit.py` |

64 covers `workspace_heavy_media_month`, `global_huggingface_monthly`,
`global_resemble_monthly`, all current subscription/new-user dimensions, user
and workspace IDs, `global`, the 48-character HMAC IP subject, and
`user_id:g<generation>`.

### `quota_reservations`

| Attribute | Type / limit | Required and legacy behavior | Writer | Code |
| --- | --- | --- | --- | --- |
| `user_id` | string, 36 | yes | Function transaction | `rate_limit.py` |
| `workspace_id` | string, 36 | optional; absent for personal admission | Function transaction | `rate_limit.py` |
| `quota_dimension` | string, 32 | yes | Function transaction | `rate_limit.py` |
| `window_start` | string, 32 | yes | Function transaction | `rate_limit.py` |
| `state` | enum/string, 16 | yes: `reserved`, `consumed`, `refunded` | Function transaction | `rate_limit.py` |

### `user_quota_generations`

| Attribute | Type / limit | Required | Writer | Code |
| --- | --- | --- | --- | --- |
| `user_id` | string, 36 | yes | Function admin transaction | `subscriptions.py`, `admin.py` |
| `quota_key` | enum/string, 32 | yes: `checks`, `heavy_media_checks` | Function | `subscriptions.py`, `admin.py` |
| `generation` | integer | yes | Function increment | `subscriptions.py`, `admin.py` |

### Workspace tables

| Table | Attribute | Type / limit | Required / default | Writer | Code |
| --- | --- | --- | --- | --- | --- |
| `workspaces` | `owner_user_id` | string, 36 | yes | Function transaction | `workspaces.py` |
|  | `name` | string, 120 | yes | Function transaction | `workspaces.py` |
|  | `member_count` | integer | yes, default `0` | Function increment | `workspaces.py` |
|  | `quota_plan` | enum/string, 16 | optional; missing means `free` | server-managed only | `subscriptions.py` |
|  | `quota_overrides` | string, 1024 | optional; missing means `{}` | server-managed only | `subscriptions.py` |
|  | `provider_quota_overrides` | string, 1024 | optional JSON: provider → общий месячный лимит команды | Function owner only | `workspaces.py`, `rate_limit.py` |
| `workspace_memberships` | `workspace_id`, `user_id` | string, 36 | yes | Function transaction | `workspaces.py` |
|  | `role` | enum/string, 16 | yes: `owner`, `member` | Function | `workspaces.py` |
|  | `status` | enum/string, 16 | yes: `active` | Function | `workspaces.py` |
| `workspace_invitations` | `workspace_id`, `inviter_user_id` | string, 36 | yes | Function | `workspaces.py` |
|  | `email` | email/string, 320 | yes, canonical lowercase | Function | `workspaces.py` |
|  | `status` | enum/string, 16 | `pending`, `accepted`, `rejected`, `cancelled`, `expired` | Function | `workspaces.py` |
|  | `expires_at` | datetime/string, 64 | yes | Function | `workspaces.py` |

Membership row IDs are deterministic `(workspace_id, user_id)` hashes.
Invitation IDs are deterministic `(workspace_id, canonical_email)` hashes; a
terminal invitation row is reused rather than retained as history.

### `admin_audit_log`

| Attribute | Type / limit | Required | Writer | Code |
| --- | --- | --- | --- | --- |
| `actor_user_id`, `target_user_id` | string, 36 | yes | Function admin | `admin.py` |
| `action` | string, 64 | yes | Function admin | `admin.py` |
| `old_value`, `new_value` | string, 1024 | yes | Function admin | `admin.py` |
| `created_at` | datetime/string, 64 | yes | Function admin | `admin.py` |
| `operation_key` | string, 36 | yes | Function admin | `admin.py` |
| `state` | enum/string, 16 | `pending`, `completed` | Function admin | `admin.py` |

## Query and index inventory

Cursor pagination uses Appwrite's system `$sequence` in descending insertion
order. `$sequence` is monotonically incremented for each inserted row, so it
is unique even when `$createdAt` timestamps coincide. `cursorAfter` remains
the opaque `$id` of the last returned row, as required by TablesDB; it is not
an authorization input.

| Table | Filters | Ordering / cursor | Required index |
| --- | --- | --- | --- |
| `checks`, personal | `user_id=actor`, `workspace_id IS NULL` | `$sequence DESC`, `cursorAfter($id)` | `(user_id, workspace_id, $sequence)` |
| `checks`, workspace owner | `workspace_id=W` | `$sequence DESC`, cursor | `(workspace_id, $sequence)` |
| `checks`, workspace member | `workspace_id=W`, `user_id=actor` | `$sequence DESC`, cursor | `(workspace_id, user_id, $sequence)` |
| `workspace_memberships`, my list | `user_id`, `status=active` | `$sequence DESC`, cursor | `(user_id, status, $sequence)` |
| `workspace_memberships`, member list/email check | `workspace_id`, `status=active` | ASC for member list; bounded unordered email check | `(workspace_id, status, $createdAt)` |
| `workspace_invitations`, owner | `workspace_id` | `$sequence DESC`, cursor | `(workspace_id, $sequence)` |
| `workspace_invitations`, recipient | `email`, `status=pending` | `$sequence DESC`, cursor | `(email, status, $sequence)` |
| `users`, admin list | optional exact `email` or `$id` | `$sequence DESC`, cursor | `$sequence`; `(email, $sequence)` for email search; `$id` is system ID |
| `admin_audit_log` | optional `target_user_id` | `$sequence DESC`, cursor | `(target_user_id, $sequence)` and `$sequence` |
| `quota_reservations` active admin usage | `user_id`, `state=reserved` | `$createdAt DESC`, limit 50 | `(user_id, state, $createdAt)` |

All other canonical reads are direct row-ID reads and need no secondary index:
`rate_limits`, reservation lifecycle, generations, profile/member/workspace/
invitation lookup, and global provider counters. No runtime query lists
reservations by `workspace_id`; do not create an unused index. Admissions and
generation resets use TablesDB transactions but do not list rows.

For an unchanged result set, a cursor continuation contains each matching row
at most once and does not skip rows because the order key is unique. With a
new insert between pages under descending order, the new row is before the
previous cursor and is intentionally not injected into the existing traversal;
it appears after refresh/new traversal. Cursor pagination is not snapshot
isolation. A malformed cursor is rejected by request validation; a deleted or
out-of-scope cursor is sent only as an opaque ID with the original equality
filters, and any Appwrite non-2xx response maps to the existing controlled
history/workspace/admin unavailable boundary. No cross-scope data can be read.

The former `$createdAt` indexes for checks, my-workspace memberships,
invitations, users and audit are no longer required by canonical paginated
queries after the `$sequence` indexes are available. Keep them through rollout
and remove only after verifying no external/live query still uses them.
`(workspace_id, status, $createdAt)` remains required for the non-paginated
member list, and `(user_id, state, $createdAt)` remains required for the fixed
active-reservations usage view.

## Permissions and Function API key

| Tables | Browser table/row permissions | New rows | Writer |
| --- | --- | --- | --- |
| `checks` | read/create/update/delete: none | `[]` | Function |
| `workspaces`, memberships, invitations | read/create/update/delete: none | `[]` | Function |
| rate limits, reservations, generations, audit | read/create/update/delete: none | `[]` | Function |
| `users` | preserve legacy profile contract; no client policy/count writes | legacy owner-read may remain | Function |

The Function key needs only TablesDB row read/write scopes (`rows.read` and
`rows.write` in this project’s scope naming). The transaction endpoint is used
only to perform those row operations; the Function does not create tables,
attributes, indexes, or ACLs and needs no administration scope. Confirm the
equivalent scope names for the deployed Appwrite version before issuing the
key.

### Personal history Function contract

Browser clients must use the authenticated analyze Function, never direct
`checks` TablesDB reads or mutations. The runtime JWT is the only actor
identity; personal history requests must not include `userId`.

| Action | Request | Response |
| --- | --- | --- |
| `list_my_history` | `pageSize` 1–100, optional opaque `cursorAfter` | `{checks, next_cursor, page_size}`; each summary contains the explicit safe check fields and `explanation`, but not `details` |
| `get_my_check` | `checkId` | the same safe fields plus `details` as the persisted bounded JSON string or `null` for an absent/non-string legacy value |
| `delete_my_check` | `checkId` | `{check_id, deleted: true}` |

Malformed legacy `details` strings remain response data rather than failing a
history request; the client must parse them defensively and render the core
result when optional Complex detail is unavailable. These DTOs never expose
Appwrite `$permissions` or other raw row metadata.

## Deployment order, compatibility and blockers

1. Create missing attributes as optional where old rows exist and wait for
   `available`.
2. Create every listed index, including the `$sequence` cursor indexes, and
   wait for `available` before deploying the code that uses them.
3. Configure table IDs and a Function key with row read/write only.
4. Deploy backend only after `checks.workspace_id` and
   `quota_reservations.workspace_id` exist.
5. Deploy the frontend migration to `list_my_history`, `get_my_check`, and
   `delete_my_check` atomically with the backend release: list summaries use
   `explanation`, while details use the full safe DTO with `details`. The old
   frontend cannot read newly created `permissions: []` rows directly.
6. Remove browser access on checks/workspace/infrastructure tables; inspect
   legacy row ACLs separately because `permissions: []` affects only new rows.
7. Run live smoke for profile bootstrap, personal/workspace history, invite
   lifecycle, concurrent member capacity, provider exhaustion, and denied
   browser TablesDB calls.

Hard blockers: all required tables/attributes/indexes, Function scopes, and
A2 frontend sequencing. Backward-compatible optional absence is restricted to
`users.subscription`, `users.quota_overrides`, `users.quota_usage_generations`,
`workspaces.quota_plan`, and `workspaces.quota_overrides`, each with the exact
fallback above. Live schema/ACL state remains unverified by this repository.

## Provider budget configuration

`GLOBAL_RESEMBLE_DAILY=50`, `GLOBAL_RESEMBLE_MONTHLY=1500`,
`GLOBAL_HUGGINGFACE_DAILY=50`, and `GLOBAL_HUGGINGFACE_MONTHLY=1500` are
internal conservative safety defaults following the existing project pattern.
They are not claims about vendor quota, price, or tariff. Production must
review/override them for the actual provider account budget; invalid values
fail closed.
