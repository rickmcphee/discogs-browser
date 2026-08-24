# Per-user collection price storage (`library_items.price_paid`)

**Date:** 2026-08-09
**Status:** Shaping
**Base branch:** `worktree-collection-wishlist-filter` (stacked — see Prerequisites)

## Problem

`catalog.discogs_price` is a global column holding a per-user value. That contradiction causes live, recurring cross-tenant data loss.

`catalog` is keyed on `discogs_id` alone and has no `user_id` column (`backend/db.py`, `GLOBAL_SCHEMA`). But `discogs_price` does not hold a Discogs marketplace figure. It holds the contents of a custom Discogs collection field the user must have named exactly `"Price"`, resolved per-sync:

- `crawl_manager._sync_collection` looks the field up per user, matching `name.lower() == "price"`, and gets `price_field_id` or `None`.
- `discogs.parse_release(item, price_field_id=...)` reads that field out of the user's own collection item, yielding `None` whenever `price_field_id` is `None`.

### The live bug

When a user has no custom field named `"Price"`, `price_field_id` is `None`, so `parse_release` yields `discogs_price = None`, and the collection loop's `upsert_catalog_release(conn, release)` writes that `None` straight over the shared global row.

So any user who syncs a collection without a `"Price"` field erases the recorded price of every release they share with a user who has one. It is not a one-time corruption: it recurs on every sync, so there is no durable manual remediation. A `mode="all"` sync by the affected user restores the value, and the next sync from the other account destroys it again.

`0d7c411` (rebased to `5dbff8e` on `worktree-collection-wishlist-filter`) fixed the *wantlist* loop's version of this by adding `preserve_price: bool` to `upsert_catalog_release`, set only by the wantlist sync loop. It deliberately scoped out the collection-loop vector, because the real repair is storage, not another flag. Its own comment says so: "The flag narrows the cross-tenant overwrite without closing it."

### Why `library_items` is the right home

The value is already only ever *read* through the user's own `library_items` join:

- `get_stock_items` projects it as a scalar subquery over `_library_match_fragment(user_id, 'collection')`, and pins the sort expression to collection scope.
- `get_library_releases` already joins `library_items li` and filters `li.user_id`.

Both read paths are per-user today. Only the write path is global. Moving the value to `library_items` therefore fits the existing queries without restructuring them, and inherits the isolation the table already has.

## Prerequisites

This work stacks on `worktree-collection-wishlist-filter` (`f0e0083`), not `main`. Three landmarks it modifies exist only there:

| Landmark | Introduced by | On `main`? |
|---|---|---|
| `_library_match_fragment(user_id_param, library_scope)` | `de51329` | No — `main` has `_not_owned_clause` only |
| `discogs_price` scalar subquery + collection-pinned sort in `get_stock_items` | `de51329` / `fc75a8c` | No |
| `upsert_catalog_release(..., preserve_price: bool)` | `5dbff8e` | No |

Steps 3 and 5 below are not implementable against `main`. This PR should not merge before the wishlist-filter PR does.

## Design

### 1. Storage

Add to `TENANT_SCHEMA`, alongside the existing additive `library_items` migrations:

```sql
ALTER TABLE library_items ADD COLUMN IF NOT EXISTS price_paid TEXT;
```

`TEXT`, not `NUMERIC`: the value is a free-text Discogs custom field whose contents the app does not control, matching the `TEXT` type `catalog.discogs_price` already used. The existing stock sort already regex-extracts a number from it (`regexp_match(..., '\d+\.?\d*')`), and that behaviour is preserved rather than replaced by a typed column.

**No grant or RLS changes are needed.** `app_user` already holds table-level `GRANT SELECT, INSERT, UPDATE, DELETE ON library_items`, which covers new columns, and the `library_items_isolation` RLS policy is row-scoped, so it protects `price_paid` on creation. This is the substance of the fix, not an incidental benefit: the column becomes unwritable across tenants by construction, rather than by a caller remembering to pass a flag.

### 2. Write path

`upsert_library_item` gains a `price_paid` parameter defaulting to a module-level `_UNSET` sentinel, **not** to `None`.

The sentinel is load-bearing, and the reason is worth stating because the obvious alternative is wrong. The neighbouring parameters use `COALESCE(%(param)s, library_items.param)`, where `None` means "unspecified, inherit". Price cannot use that pattern: `parse_release` legitimately yields `None` for a user who has cleared their Discogs `"Price"` field, so under `COALESCE` a price would inherit forever and become permanently unclearable through the app. `None` therefore has to mean "authoritatively empty", which leaves nothing to mean "unspecified" — hence the sentinel.

The `SET` clause for the column is included only when the caller passed something:

```python
    price_set = "" if price_paid is _UNSET else "price_paid = %(price_paid)s,"
```

giving three distinct behaviours from one parameter:

| Caller | Effect |
|---|---|
| Omits `price_paid` | Column absent from `SET`; existing value inherited. `NULL` on first insert. |
| Passes a value | Written. |
| Passes `None` explicitly | Written as `NULL` — a genuine clear. |

Call sites:

| Call site | Passes `price_paid`? | Why |
|---|---|---|
| Collection loop, full-parse path (`crawl_manager.py:429`) | Yes — `release["price_paid"]` | The per-user `price_field_id` result is already in scope here |
| Collection loop, `mode="new"` skip path (`:409`) | No | Never calls `parse_release`; no price is in scope |
| Wantlist loop (`:471`) | No | Wantlist items carry no collection price field |

**On `mode="new"` and clearing semantics.** The `mode="new"` early-continue path calls `upsert_library_item` without ever calling `parse_release`, so it has no price to pass. Omitting the parameter inherits the stored value — consistent with the fact that this path also declines to refresh `artist`, `title`, and `barcode` for already-known releases.

Clearing works on any `mode="all"` sync, because the full-parse path always passes the parameter and `None` there means the field really is empty.

This is deliberately *not* done with a `price_paid_known: bool` companion flag. That would be the shape of `preserve_price`, which step 5 retires. The sentinel expresses the same distinction through the parameter's own absence, so there is no second argument for a caller to get out of step with the first.

**`parse_release` renames its output key** `discogs_price` → `price_paid`. This is what allows `preserve_price` to die: once `upsert_catalog_release` has no price to write, the flag guards nothing.

Churn note: the existing test fixtures that pass `"discogs_price": None` into `upsert_catalog_release` keep working untouched, because psycopg ignores named parameters the statement does not reference. Only tests that assert on `catalog.discogs_price` need editing.

### 3. Read path

Both queries keep `discogs_price` as the wire/JSON field name, so `frontend/src/api/types.ts`, `RecordBrowser.tsx`'s sort key, and the frontend test fixtures are untouched. The DB column and the wire name diverge; that is accepted to keep this change scoped to the data-loss fix.

**`get_library_releases`** — already joins `library_items li`:

```sql
SELECT c.*, li.price_paid AS discogs_price, li.plex_url, ...
```

The query must also keep a unique final `ORDER BY` term (`c.discogs_id`). This change sharpens an existing pagination hazard rather than creating it: a sort key that is NULL for every row leaves all rows tied, the `ORDER BY` unspecified, and Postgres' bounded top-N sort free to return a different arbitrary order per page, so `LIMIT`/`OFFSET` pages repeat and drop rows. After this change `price_paid` is NULL for every row until each user re-syncs, and the backfill deliberately leaves contested rows NULL, so a Price sort ties nearly every user's whole collection. The term is the fix in PR #79, which this branch conflicts with on that exact line — see the plan for the resolution rule. `get_stock_items` already carries the equivalent as `s.id`.

`discogs_price` moves out of `_RELEASE_ALLOWED_SORT` (it can no longer take the `f"c.{sort_col}"` path, since it is no longer a `c.` column) into a `sort_expr` special case, structurally like the existing `date_added` case:

```python
if sort == "discogs_price":
    sort_expr = "li.price_paid"
elif sort == "date_added" and scope in ("discogs", "wishlist"):
    ...
```

**`get_stock_items`** — `_library_match_fragment` already binds `li`, so both sites repoint with no structural change:

- Projection: `(SELECT li.price_paid {_library_match_fragment('%(user_id)s', 'collection')} LIMIT 1) AS discogs_price`
- Sort: `regexp_match(li.price_paid, ...)` in place of `regexp_match(c.discogs_price, ...)`

The collection-pinned sort guard (`"in_collection" in _LIBRARY_MEMBERSHIP.get(library_scope, ())`) and its rationale are unchanged and still correct: under wishlist scope every key would be NULL, leaving rows tied and pagination unstable.

### 4. Backfill and retirement — one self-retiring migration

The existing global values are whichever user synced most recently. Copying them to every owner of a release would be wrong — it attributes one user's price to others, which is the same cross-tenant leak in reverse, writing incorrect data that then looks authoritative. Dropping them outright discards recoverable data.

**Backfill only where exactly one user has the release in their collection** — the one case where the global value provably belongs to that user:

```sql
UPDATE library_items li SET price_paid = c.discogs_price
FROM catalog c
WHERE c.discogs_id = li.discogs_id
  AND li.in_collection = TRUE
  AND c.discogs_price IS NOT NULL
  AND (SELECT COUNT(*) FROM library_items x
       WHERE x.discogs_id = li.discogs_id AND x.in_collection = TRUE) = 1;
```

Contested releases are left NULL and self-heal on the owner's next `mode="all"` sync. The statement runs on the admin pool (`init_tenant_schema` uses `get_admin_pool()`, whose role is `BYPASSRLS`), so the cross-user `COUNT(*)` is not filtered by RLS.

**Ordering constraint.** The backfill must read `catalog.discogs_price` before the drop removes it, but `TENANT_SCHEMA` re-runs on every boot. A bare `UPDATE` would therefore either error once the source column is gone, or keep re-filling values a user had deliberately cleared.

So the backfill and the drop go in **one `DO` block, guarded on the source column still existing**:

```sql
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name = 'catalog' AND column_name = 'discogs_price') THEN
    UPDATE library_items li SET price_paid = c.discogs_price
    FROM catalog c
    WHERE c.discogs_id = li.discogs_id
      AND li.in_collection = TRUE
      AND c.discogs_price IS NOT NULL
      AND (SELECT COUNT(*) FROM library_items x
           WHERE x.discogs_id = li.discogs_id AND x.in_collection = TRUE) = 1;

    ALTER TABLE catalog DROP COLUMN discogs_price;
  END IF;
END $$;
```

First boot does the work; every later boot no-ops because the column is gone. This keeps the change a normal `bootstrap.sh` redeploy with no manual sysadmin step, honouring the invariant that `bootstrap.sh` never destroys data.

**Placement across the two schema strings.** `catalog` is defined in `GLOBAL_SCHEMA` (`db.py:57-150`) while `library_items` is in `TENANT_SCHEMA` (`:159-281`), and `main.py:87-88` applies `init_global_schema()` before `init_tenant_schema()`. Three placements follow from that, and getting any of them wrong breaks the migration:

1. Remove the `discogs_price TEXT,` line from `GLOBAL_SCHEMA`'s `CREATE TABLE IF NOT EXISTS catalog` (`:65`), so a fresh database never creates the column. On an existing database `CREATE TABLE IF NOT EXISTS` is a no-op, so the column survives there until the `DO` block drops it — which is exactly the required behaviour.
2. Put `ALTER TABLE library_items ADD COLUMN IF NOT EXISTS price_paid TEXT` in `TENANT_SCHEMA` with the other additive `library_items` migrations (`:195-196`).
3. Put the `DO` block in `TENANT_SCHEMA` **after** that `ADD COLUMN`. It must not go in `GLOBAL_SCHEMA`: that runs first, when `library_items.price_paid` does not yet exist, and the backfill would fail.

The drop ships in this same change rather than a follow-up: after step 3 nothing reads `catalog.discogs_price`, so leaving it in place would be a decoy column that still looks writable and invites exactly this bug again. The accepted cost is that there is no in-place rollback — reverting the deploy leaves `price_paid` populated but the old column gone.

### 5. Retire `preserve_price`

With no price on `catalog`, `upsert_catalog_release` drops both the `discogs_price` column from its INSERT/UPDATE and the `preserve_price` parameter, along with the explanatory comment block that exists only to justify the flag. The wantlist loop's `upsert_catalog_release(conn, release, preserve_price=True)` becomes a plain call.

## Testing

Backend tests need Postgres running and `TEST_DATABASE_URL`, `IDENTITY_DB_PASSWORD`, and `APP_DB_PASSWORD` in `backend/.env` (all three, per the test-database-freshness work that landed in #81 — each pytest session now builds its own database and rewrites both app roles' passwords).

That harness also removes a caveat this spec's migration tests were written around. Previously the local test database persisted across runs and its fixtures only truncated rows, never dropped columns, so a schema assertion could pass on a stale column even if its migration were reverted. Each session now starts from `TEMPLATE template0`, so the tests below genuinely exercise the migration. The `_readd_legacy_catalog_price` helper is still required — it constructs the pre-migration shape deliberately, rather than inheriting it.

- `upsert_library_item` writes `price_paid`; omitting it preserves the stored value; passing `None` explicitly clears it. All three sentinel behaviours need a test — the omit-vs-explicit-`None` pair is the whole point of the sentinel, and a `COALESCE` implementation would pass the first and fail the second.
- **Cross-tenant regression, the bug this fixes:** user A syncs with a `"Price"` field and user B syncs the same release without one; A's `price_paid` survives B's sync. This is the test whose absence allowed the bug.
- `mode="new"` sync over an existing release leaves `price_paid` intact.
- A `mode="all"` sync with `price_field_id=None` clears the syncing user's own `price_paid` and no one else's.
- `get_library_releases` returns the calling user's `price_paid` as `discogs_price`, and sorting by `discogs_price` orders on it.
- `get_stock_items` projects and sorts the calling user's value under collection scope.
- Backfill: single-owner release is copied; a release held by two users is left NULL for both.
- Migration idempotence: running `init_tenant_schema` twice does not error and does not re-fill a value cleared between runs.
- Fresh-database path: after `init_global_schema()` + `init_tenant_schema()` on an empty database, `catalog` has no `discogs_price` column and `library_items` has `price_paid`.

Existing `test_catalog_crud.py` assertions on `catalog.discogs_price` are removed or repointed.

## Documentation impact

`docs/superpowers/specs/2026-07-26-multi-tenant-architecture-design.md:191` describes the column as "Discogs' own marketplace figure — global". That is factually wrong and causally implicated in this bug: it makes an unconditional global overwrite look correct. The `catalog` data dictionary loses its `discogs_price` row; a `library_items.price_paid` row is added, described as the contents of the user's own custom Discogs collection field named `"Price"`. The column list at `:396` also needs the removal.

`docs/superpowers/specs/2026-06-27-discogs-browser-design.md:87` describes the semantics correctly ("User's purchase price from Discogs collection field") but attributes them to the wrong table, and moves with the column.

The repo's required pre-PR spec-drift check sweeps for the remainder.

**Runtime/agent docs:** this repo has no `AGENTS.md` and no `.agents/` directory, so `INPUTS.md`, `OUTPUTS.md`, and `INSTRUCTIONS.md` are not affected. The change adds no trigger, no external call, and no new command or configuration, so the root `README` is unaffected. `CLAUDE.md` needs no change — no invariant it records is altered.

## Out of scope

- Renaming the `discogs_price` wire/JSON field to `price_paid`. Deliberate: it would touch `api/types.ts`, `RecordBrowser.tsx` sort keys, and the frontend test fixtures, none of which bears on the data loss.
- Typing the column as `NUMERIC` or normalizing currency. The value is free text from a user-controlled Discogs field.
- Any live Discogs marketplace price. No such data exists in this app; the misconception that it does is what the spec correction above addresses.
