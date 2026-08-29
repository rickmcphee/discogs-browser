# Saved-item price drop notifications — implementation plan

**Goal:** A per-user Notifications tab, reached from a bell in the header, that
tells a user when one of their saved Store items becomes the cheapest it
currently is anywhere, and links straight to the listing that undercut the rest.
A red dot on the bell while anything is unread.

**Architecture:** The crawl worker records price drops into a *global*
`stock_item_price_drops` table — it cannot fan them out per user, because
`stock_item_saves` is RLS-scoped and invisible to its unscoped `app_user`
connections. Each user's notifications are then a read-time join through their
own saves under `user_scope()`, so isolation comes from the RLS policy that
already exists rather than from a new bypass. Read state is one watermark row
per user.

**Tech Stack:** FastAPI + psycopg on the backend, React 19 + TypeScript +
Tailwind 4 on the frontend, pytest and Vitest. No new dependency.

**Design spec:** [`docs/specifications/shaping/2026-08-28-price-drop-notifications-design.md`](../shaping/2026-08-28-price-drop-notifications-design.md)

**Verified against:** `main` @ `36c8494`.

## Global constraints

- **The floor is read before the write it is a baseline for.** That is why
  detection lives inside the three `db.py` write helpers rather than at their
  call sites, and why `replace_stock_items`' key-computing loop now runs ahead
  of its `DELETE`. Reading it afterwards on that path would find no prior price
  for anything and report the whole catalog as having just got cheaper.
- **No new RLS bypass, and no loosening of `stock_item_saves_isolation`.** If a
  change seems to need the worker to see who saved what, it is the wrong change.
- **No new SSE event.** A per-user one would need an owner tag the worker cannot
  determine; the badge rides `priceGeneration`, a strict subset of the
  `stockSyncGeneration` the Store and Track tabs refetch on, carrying only the
  events that follow a real price write.
- **The Notifications view is mounted only while active.** Loading it is what
  marks its rows read.
- Backend checks run from `backend/`: `pytest`. Frontend checks run from
  `frontend/`: `npm run test`, `npm run build`, `npm run lint`.

## File structure

| File | Task(s) | Responsibility after this plan |
|---|---|---|
| `backend/db.py` | 1, 2, 3 | Both tables, grants, RLS policy; `_price_floors`, `_record_price_drops`, `delete_expired_price_drops`; the read/watermark helpers |
| `backend/main.py` | 3, 4 | `_price_drop_sweep_loop`, the hourly retention task; router registration |
| `backend/routers/notifications.py` | 4 | The three endpoints |
| `frontend/src/api/types.ts`, `client.ts` | 5 | `PriceDropNotification` and the three client functions |
| `frontend/src/views/formatTimestamp.ts` | 5 | `formatServerTimestamp` (lifted from `Account.tsx`) + `formatRelativeTime` |
| `frontend/src/components/NotificationBell.tsx` | 6 | Bell button, unread dot, accessible label |
| `frontend/src/views/Notifications.tsx` | 6 | The list |
| `frontend/src/App.tsx` | 6 | `'notifications'` view, badge state, read-on-open |
| `backend/tests/test_price_drop_notifications.py` | 7 | Detection rules and the per-user read path |
| `backend/tests/test_notifications_router.py` | 7 | Endpoint payloads |
| `backend/tests/test_tenant_schema.py` | 7 | RLS on `user_notification_reads`, both directions |
| `frontend/src/test/notifications.test.tsx` | 8 | Bell, dot, view, read-on-open |
| `frontend/src/test/formatTimestamp.test.ts` | 8 | The offsetless-`TIMESTAMP` parse |
| `frontend/src/test/client.test.ts` | 8 | The three client functions |

---

### Task 1: `stock_item_price_drops` in `GLOBAL_SCHEMA`

- [x] Table plus `(item_key, id DESC)` index, after the `stock_items_item_key_idx`
      block it depends on.
- [x] `GRANT SELECT, INSERT, DELETE` to `app_user` (no `UPDATE` — append-only),
      and the sequence grant.

### Task 2: `user_notification_reads` in `TENANT_SCHEMA`

- [x] Table, `ENABLE`/`FORCE ROW LEVEL SECURITY`, `user_notification_reads_isolation`
      policy, `GRANT SELECT, INSERT, UPDATE, DELETE` to `app_user`.
- [x] De-number the "all four policies below" comment while adding a policy to
      the set it describes.

### Task 3: Detection and retention

- [x] `_normalized_currency`, `_price_floors` (bucketed by currency),
      `_record_price_drops` (strict `<`, deduped per `(item_key, currency)`).
- [x] Call both from `replace_stock_items`, `upsert_stock_item_listing` and
      `upsert_stock_item_from_release`, floor read first in each.
- [x] `delete_expired_price_drops` + `PRICE_DROP_RETENTION_DAYS`, swept hourly
      by `main._price_drop_sweep_loop`, which sweeps before its first sleep.
      Not a step in `_sync_stock`: `stock_schedule` is optional and defaults to
      empty, while the worker pool records drops through the release path
      regardless, so that version never pruned on a deployment that ran no
      catalog sync.

### Task 4: Router

- [x] `GET /notifications`, `GET /notifications/unread`,
      `POST /notifications/read`, all under `db.user_scope`.
- [x] Register in `main.py`. No `AuthMiddleware` allowlist entry — these are
      authenticated like every other `/api` route.

### Task 5: Client surface

- [x] Types, three client functions, shared timestamp helpers.

### Task 6: UI

- [x] Bell before the avatar, both layouts, every user.
- [x] View mounted only while active; read watermark written on load.
- [x] Badge refetched off `priceGeneration`, not the broader
      `stockSyncGeneration`: the judgment events bump that one too, and a
      judgment can never move a price.

### Task 7: Backend tests

- [x] Detection rules, the per-user read path, RLS both directions.

### Task 8: Frontend tests

- [x] Bell/dot/view/read-on-open, timestamp parsing, client functions.
- [x] Add the two new client exports to every App-rendering test file's
      `../api/client` mock — those mocks are exhaustive and an unlisted export
      throws on render.

### Task 9: Spec-drift sweep

- [x] Amend the saved-items spec (its "no price-drop alerts" and "no badge in
      the nav" exclusions), the mobile spec (its "the avatar is the only header
      control a non-admin has" claim and its layout table), the profile spec
      (what precedes the avatar), the multi-tenant spec (its table diagram is a
      snapshot, not an inventory), and the base design spec's directory tree.
