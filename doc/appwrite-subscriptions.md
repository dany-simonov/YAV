# Подписки и лимиты Appwrite

Canonical production backend — Appwrite Function `src/main.py`. Подписка и
индивидуальные лимиты не принимаются от браузера и хранятся в уже существующей
таблице профилей `users`.

Добавьте в таблицу `users` следующие attributes. Для существующей production
таблицы не делайте их сразу required: сначала создайте optional attributes с
defaults, выполните backfill, и только затем (при необходимости) усиливайте
ограничения.

| Attribute | Type | Size | Required | Default |
| --- | --- | ---: | --- | --- |
| `subscription` | string | 16 | after backfill | `free` |
| `quota_overrides` | string | 1024 | after backfill | `{}` |
| `quota_usage_generations` | string | 1024 | after backfill | `{}` |
| `email` | string | 320 | after backfill | empty string |

`subscription` допускает только `free`, `pro`, `enterprise`, `custom`.
`quota_overrides` — компактная JSON-строка с разрешёнными ключами `checks` и
`heavy_media_checks`, значения — положительные целые числа. Новые профили
создаются со всеми полями; существующие профили до миграции безопасно читаются
через прежнее поле `plan` (`premium` соответствует `pro`). Пустой
`subscription` также обрабатывается как legacy row.

`users.quota_usage_generations` не содержит счётчики и не является source of
truth generation. Это migration marker: его наличие включает чтение canonical
state из `user_quota_generations`. Значение остаётся `{}`; canonical generation
хранится только в отдельной таблице.

`users.email` — денормализованный search/display field, не identity и не
источник admin прав. Source of truth — Appwrite Auth account, полученный через
runtime JWT. Функция синхронизирует email при следующем authenticated вызове
пользователя; до этого exact admin search может видеть прежнее значение.

Для таблицы `users` не нужны новые client-readable индексы. System
administrator задаётся только Function environment variable
`SYSTEM_ADMIN_USER_IDS` (список Appwrite account IDs через запятую), а не
полем профиля. Функция использует runtime Appwrite JWT, проверяет `/account`
и сопоставляет `$id` с runtime user ID до проверки allowlist. Пустое либо
malformed значение deny-all; используйте только валидные IDs, например
`SYSTEM_ADMIN_USER_IDS=abc123,def456`.

Изменять subscription и overrides может только Appwrite Function с server API
key. Существующие owner permissions профиля сохраняются; не добавляйте клиенту
write permission на эти policy fields.

## Admin telemetry, reset и audit

Добавьте две server-only таблицы. Не выдавайте пользователям read/write права к
ним: Function использует server API key.

### `user_quota_generations`

| Attribute | Type | Size | Required |
| --- | --- | ---: | --- |
| `user_id` | string | 36 | yes |
| `quota_key` | string | 32 | yes |
| `generation` | integer | — | yes |

Row ID детерминированно создаётся Function из `(user_id, quota_key)`. Для
текущих ключей `checks` и `heavy_media_checks` отдельный row не нужен до
первого reset. Reset атомарно увеличивает `generation`; новые admissions пишут
в новый subject counter, а старые counters и active reservations не удаляются.
Это не позволяет reset'у повредить старый refund/consume lifecycle.

### `admin_audit_log`

| Attribute | Type | Size | Required |
| --- | --- | ---: | --- |
| `actor_user_id` | string | 36 | yes |
| `action` | string | 64 | yes |
| `target_user_id` | string | 36 | yes |
| `old_value` | string | 1024 | yes |
| `new_value` | string | 1024 | yes |
| `created_at` | datetime/string | 64 | yes |
| `operation_key` | string | 36 | yes |
| `state` | string | 16 | yes |

Создайте индексы для запросов admin UI:

- `created_at` descending — newest-first pagination;
- `target_user_id` + `created_at` — audit пользователя;
- `operation_key` — deterministic lookup/reset idempotency;
- `users.email` — exact email search в `admin_list_users`.

`users` также использует `$createdAt` для cursor pagination. Audit values —
bounded JSON только с изменяемыми subscription/quota значениями; ключи, JWT,
provider payload и исходный пользовательский контент в audit не попадают.

Обычные administrative mutations получают completed audit event. Reset actions
требуют `idempotencyKey` (16–64 ASCII букв/цифр/`._-`). До increment Function
создаёт audit row со `state=pending` и deterministic `operation_key`; после
успеха обновляет его в `completed`. Retry с тем же key после completed просто
возвращает актуальную policy. Retry при `pending` получает typed
`admin_operation_pending` и **не** выполняет второй increment.

## Safe deployment checklist

1. Создайте optional `users` attributes выше с указанными defaults; не меняйте
   старые `plan`, `name`, `email_verified` и owner permissions.
2. Создайте server-only `user_quota_generations` и `admin_audit_log` со всеми
   attributes этой страницы, включая `operation_key` и `state`. Client
   read/write permissions не выдавайте.
3. Создайте indices: `users.email`; audit `created_at` newest-first,
   `(target_user_id, created_at)` и `operation_key`.
4. Backfill существующих users небольшими batches: пустой `email` допустим,
   `subscription=free`, `quota_overrides={}`, `quota_usage_generations={}`.
   Старые `plan=premium` можно не переписывать: код читает его как `pro`.
5. Настройте Function env: `APPWRITE_USER_QUOTA_GENERATIONS_TABLE_ID`,
   `APPWRITE_ADMIN_AUDIT_TABLE_ID`, `SYSTEM_ADMIN_USER_IDS` и все policy limits
   из `.env.example`.
6. Только затем deploy Function. Smoke: normal user `get_my_subscription` и
   denied admin action; admin list/detail, plan/override mutation, reset с
   новым `idempotencyKey`, audit list; legacy `plan=premium` profile.
