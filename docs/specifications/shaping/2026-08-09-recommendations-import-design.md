# Recommendations import design

Date: 2026-08-09
Branch: `recommendations-import`

## Problem

Recommendation judgments cost real money. Every unjudged Store item that
`_run_judgment_phase` picks up is an Anthropic API call
(`recommendations.judge_batch`, `claude-haiku-4-5`, batches of 40), and a
9000-item catalog is a meaningful bill. Once spent, that value lives in
exactly one place: `stock_item_judgments` rows in one Postgres database, for
one `user_id`.

There is no way to get it back out and in again. Today's
`GET /api/stock/export` produces a shopping list, not a backup:

- it emits only `recommended = TRUE` rows, so every "not recommended"
  verdict — which cost exactly as much to obtain, and which is what
  suppresses re-judging on the next run — is absent;
- it omits `item_key`, the only key `stock_item_judgments` is addressable
  by;
- it filters through `_not_owned_clause`, so judgments for items the user
  has since acquired silently drop out.

So a user who wipes their instance, moves to a new one, or runs a second
instance (dev alongside hosted) re-pays the full bill. This slice makes the
spend portable: export becomes a complete judgment ledger, and a new import
loads one back.

## Scope

Touches:

- `backend/db.py` — new `get_all_stock_judgments()` (export) and
  `import_stock_judgments()` (import upsert); one new index,
  `stock_items_item_key_idx`. `get_recommended_stock_items()` is left alone
  — it keeps serving its own purpose and is no longer the export query.
- `backend/routers/stock.py` — `GET /api/stock/export` re-pointed at the new
  query and widened to 10 columns; new `POST /api/stock/import`.
- `backend/recommendations_import.py` — new module: CSV parsing, per-row
  validation, in-file de-duplication. Kept out of the router so it is
  unit-testable without HTTP.
- `frontend/src/api/client.ts` — `importRecommendationsCsv(file)`.
- `frontend/src/api/types.ts` — `RecommendationImportResult`.
- `frontend/src/views/Account.tsx` — new "Import" row in the
  Recommendations section, **between Export and Clear**; updated help text
  for Export's now-wider column list.
- `frontend/src/App.tsx` — `handleImportRecommendations`, reporting through
  the existing `setSyncStatus` channel like its three siblings.
- `README.md` — export/import format.
- `docs/superpowers/specs/2026-07-06-store-recommended-filter-design.md` —
  amendment, since §6 "Export Recommendations action" specifies the old
  7-column shape.
- `backend/version.py` — minor bump.
- Tests: `backend/tests/test_recommendations_import.py` (new),
  `backend/tests/test_stock_router.py`, `backend/tests/test_judgment_crud.py`,
  `frontend/src/test/account.test.tsx`.

Out of scope: any change to how judgments are *produced* (prompt, batching,
model, item limit); sharing or publishing judgments between users inside one
instance; importing anything other than judgments (stock catalog, listings,
library are all re-derivable for free from Discogs and the crawlers, so
they carry no preserved spend).

## Decisions

**Export becomes the import format; there is one file, not two.** The
existing `recommendations.csv` gains three columns rather than growing a
sibling JSON backup file. It stays spreadsheet-openable, and there is only
one thing for a user to keep. The cost is that the file no longer matches
the Store tab's `Recommended` view row-for-row — it now contains
not-recommended rows and owned items too. Account's help text has to say so
plainly.

**`item_key` is a required column, because it cannot be recomputed.**
`compute_item_key()` hashes `artist|title|url`, and `replace_stock_items()`
deliberately feeds it the *legacy* `item["artist"].title()` casing while
storing the corrected `normalize_artist_casing()` output in the row (see the
comment at `backend/db.py:850`). The export therefore emits normalized
artist/title that will not reproduce the key. There is no derivation path
from the human-readable columns back to the key; the literal hash must
travel in the file. Corollary: hand-authoring an import file, or importing
one produced by a differently-normalizing crawler version, will not match
anything. The response's `matched_stock_items` count is the user's only
signal that this happened, which is why it exists.

**Newest `judged_at` wins.** Export gains a `judged_at` column and the
import upsert applies a row only when the file's timestamp is strictly newer
than the local one. This makes "my other instance is more current" resolve
correctly without asking the user anything, and makes the operation
idempotent — re-importing the same file a second time changes nothing. It
does mean an import can flip a verdict the user is currently looking at;
that is the accepted trade for not needing a merge-strategy UI.

This forces a **new** db helper rather than reuse of
`upsert_stock_judgments()`, which stamps `judged_at = CURRENT_TIMESTAMP`.
Reusing it would erase the imported timestamps, breaking newest-wins on the
next round-trip and making every imported row look freshly judged.

**Unknown `item_key`s are accepted.** `stock_item_judgments` has no foreign
key to `stock_items`, so a verdict for an item this instance has never
crawled inserts fine. This is where most of the preserved value sits: store
stock churns, `replace_stock_items()` deletes and reinserts each crawler's
rows on every sync, so at any moment most historical judgments have no live
stock row. Accepting them means that when a sync next surfaces the item, it
arrives already judged and never costs money.

The visible consequence must be reported honestly: the `Recommended` filter
inner-joins `stock_items` (`get_stock_items`), so importing thousands of
verdicts onto a freshly-synced instance can change *nothing* on screen until
the next store sync. The response therefore returns `matched_stock_items`,
and the UI states it.

No retention/pruning job for unmatched rows. The table is one row per
judged item per user; even a 9000-item catalog judged repeatedly is small,
and a pruning window would need a new config knob and a scheduled task to
solve a problem that does not exist yet.

**Import writes only to `stock_item_judgments`.** It is tempting to also
populate `stock_item_identities` from the file's artist/title/format, so
imported-but-never-seen judgments export readably later. Rejected:
`stock_item_identities` is a *global* table with no RLS policy, referenced
by `crawl_queue.item_key` and `listings.item_key`. Letting one tenant's
uploaded file write rows into shared global state — under keys of its own
choosing — is a tenant-isolation hole for a cosmetic gain. Every table this
import touches is RLS-scoped to the caller.

**Best-effort rows, all-or-nothing header, hard byte cap.** A row that fails
validation is skipped and counted; the rest still import. A file whose
header lacks a required column is rejected whole, since every row in it
would be bad and a per-row error list one entry long per row is not a useful
answer. Independently of that, the request body is capped — this is a hosted
multi-tenant app and an uncapped upload endpoint is a memory-exhaustion
vector.

**Accepted risk: CSV formula injection.** `reason` is model-authored text.
A value beginning `=`, `+`, `-`, or `@` is interpreted as a formula by
Excel/Sheets. This is already true of today's export and is not introduced
here. Not mitigated, deliberately: the standard fix (prefixing a `'`) would
corrupt the value on round-trip, which is precisely what this feature exists
to protect. The file is the user's own data, written by a model they paid
for, opened by them. Documented rather than half-fixed.

**Taste is not transferable, and the UI says so.** A judgment is relative to
the judging user's collection and wantlist (`get_taste_listing`). Importing
a file from another person imports their taste, not just their spend. The
help text notes this; nothing in the code prevents it, since the primary use
case — one person, two instances — is indistinguishable from it.

## File format

Header, 10 columns. The existing seven keep their names and positions; three
are appended, so anything currently reading the file by column name or
leading position is unaffected:

```
artist,title,format,price,source,link,reason,item_key,recommended,judged_at
```

- `artist`, `title`, `format` — from `stock_item_identities` (durable; keyed
  by `item_key`, never deleted). Blank only for a judgment whose item this
  instance has never crawled — i.e. one that arrived by import.
- `price`, `source`, `link` — from the most recently seen `stock_items` row
  for that `item_key`. Blank when the item is not currently in stock.
  Informational only; ignored on import.
- `reason` — `stock_item_judgments.reason`, may be empty.
- `item_key` — 64 lowercase hex chars. The join key.
- `recommended` — `true` / `false`. Written as an explicit lowercase
  literal, not by handing the boolean to `csv.writer` — that would emit
  Python's `True`/`False` and break the documented format. (The importer
  accepts either spelling regardless, but the file this repo produces must
  match what this section says.)
- `judged_at` — ISO-8601, naive UTC as stored (`2026-08-09T14:03:22.481923`).

Rows: **every** judgment for the calling user. No `recommended` filter, no
`_not_owned_clause` filter. Ordered by artist (nulls last), title,
`item_key` — so the readable rows sort naturally and import-only rows
collect at the end.

On import, only `item_key`, `recommended`, `judged_at`, and `reason` are
read. The other six columns exist for humans.

## Backend design

### Export query

`get_all_stock_judgments(conn, user_id)` drives *from* the judgments table,
so rows with no stock presence survive:

```sql
SELECT
    COALESCE(i.artist, '') AS artist,
    COALESCE(i.title, '')  AS title,
    COALESCE(i.format, '') AS format,
    d.price, d.source, d.url,
    j.reason, j.item_key, j.recommended, j.judged_at
FROM stock_item_judgments j
LEFT JOIN stock_item_identities i ON i.item_key = j.item_key
LEFT JOIN LATERAL (
    SELECT s.price, cr.site_name AS source, s.url
    FROM stock_items s
    JOIN crawlers cr ON cr.id = s.crawler_id
    WHERE s.item_key = j.item_key
    ORDER BY s.last_seen DESC
    LIMIT 1
) d ON TRUE
WHERE j.user_id = %(user_id)s
ORDER BY i.artist ASC NULLS LAST, i.title, j.item_key
```

`LEFT JOIN LATERAL ... LIMIT 1` replaces the old
`DISTINCT ON (s.item_key)` subquery. Same purpose — `item_key` is not unique
in `stock_items`, so an unguarded join duplicates a judgment once per
crawler that saw the item — but per-judgment instead of deduplicating the
whole table, and it drops out entirely for the unmatched rows this export
now includes.

That lateral needs an index; `stock_items` has none on `item_key` today:

```sql
CREATE INDEX IF NOT EXISTS stock_items_item_key_idx ON stock_items (item_key);
```

Added to `SCHEMA` alongside the other `CREATE INDEX IF NOT EXISTS`
statements, per the repo's idempotent-DDL-only convention. It also helps the
existing `get_stock_items` / `get_unjudged_stock_items` joins on
`s.item_key`.

### Parsing (`backend/recommendations_import.py`)

```python
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ROWS = 100_000
REQUIRED_COLUMNS = {"item_key", "recommended", "judged_at"}
MAX_REPORTED_ERRORS = 10
```

`parse_judgment_csv(text) -> tuple[list[dict], list[dict], int]` returning
`(judgments, errors, skipped)`:

- `csv.DictReader`. If `REQUIRED_COLUMNS - set(reader.fieldnames)` is
  non-empty, raise `InvalidImportError` naming the missing columns — the
  router turns that into a 422.
- Per row, in order, first failure wins and the row is skipped with
  `{"line": reader.line_num, "error": "..."}`:
  - `item_key` matches `^[0-9a-f]{64}$`;
  - `recommended` in `{true, false, t, f, yes, no, 1, 0}`, case-insensitive;
  - `judged_at` parses. Python ≥3.9 is the floor here, and
    `datetime.fromisoformat` does not accept a trailing `Z` before 3.11, so
    a trailing `Z` is rewritten to `+00:00` first. An offset-aware result is
    converted to UTC and stripped to naive, matching the column's
    `TIMESTAMP` (no tz) type; a naive result is taken as UTC as-is.
  - `reason` — optional column; empty string becomes `None`.
- Row count over `MAX_ROWS` raises `InvalidImportError` (whole-file
  rejection, not truncation — a silently truncated import is worse than a
  refused one).
- **In-file de-duplication, keeping the newest `judged_at` per
  `item_key`.** Not optional: Postgres raises `ON CONFLICT DO UPDATE command
  cannot affect row a second time` if one statement presents the same
  conflict target twice, so a file containing a duplicated key would fail
  the entire import. Dropped duplicates count toward `skipped` with a
  `duplicate item_key` error.

Only the first `MAX_REPORTED_ERRORS` errors are returned; `skipped` is the
full count.

### Import upsert

```python
def import_stock_judgments(conn, user_id, judgments) -> tuple[int, int]:
```

One statement over parallel arrays, so the whole import is a single
round-trip and a single transaction:

```sql
INSERT INTO stock_item_judgments (user_id, item_key, recommended, reason, judged_at)
SELECT %(user_id)s, k, r, rs, ja
FROM unnest(
    %(keys)s::text[], %(recommended)s::boolean[],
    %(reasons)s::text[], %(judged_at)s::timestamp[]
) AS t(k, r, rs, ja)
ON CONFLICT (user_id, item_key) DO UPDATE SET
    recommended = EXCLUDED.recommended,
    reason      = EXCLUDED.reason,
    judged_at   = EXCLUDED.judged_at
WHERE EXCLUDED.judged_at > stock_item_judgments.judged_at
RETURNING (xmax = 0) AS inserted
```

`xmax = 0` distinguishes a fresh insert from an update, giving
`imported` / `updated` from one pass. Rows filtered out by the `WHERE` —
those whose local judgment is already at least as new — return nothing, so
`unchanged = len(judgments) - len(returned)`.

`matched_stock_items` is a separate count of how many of the file's valid
keys exist in local stock, which is what determines whether the user sees
anything change:

```sql
SELECT COUNT(DISTINCT item_key) FROM stock_items WHERE item_key = ANY(%(keys)s)
```

### Endpoint

```python
@router.post("/stock/import")
async def import_recommendations(request: Request, file: UploadFile = File(...)):
```

- Scoped to `request.state.user_id`; runs under `db.user_scope(user_id)`, so
  RLS is the backstop against writing another tenant's rows even if the
  parameter were wrong.
- Busy guard mirroring `clear_stock_judgment` exactly — if
  `crawl_manager.judgment_running(user_id)` or
  `crawl_manager.stock_sync_running`, return `200` with
  `{"imported": 0, ..., "running": True}` and write nothing. A concurrent
  judgment run would otherwise race the upsert on the same rows.
- Size cap mirroring `upload_avatar` (`backend/routers/session.py:230`):
  `data = await file.read(MAX_UPLOAD_BYTES + 1)`, then reject on
  `len(data) > MAX_UPLOAD_BYTES`. Reading cap+1 rather than the whole body
  keeps an oversized upload from being buffered in full. Oversize is a
  `413`, not a `422` — it is a transport-level refusal, and nothing was
  parsed.
- Decode as UTF-8 with `errors="replace"`, tolerating a BOM
  (`utf-8-sig`) — spreadsheet round-trips add one.
- `InvalidImportError` (missing header column, row count over `MAX_ROWS`)
  → `422` with the message.

Response:

```json
{
  "imported": 1240, "updated": 87, "unchanged": 12,
  "skipped": 3, "errors": [{"line": 44, "error": "..."}],
  "matched_stock_items": 340, "running": false
}
```

## Frontend design

`client.ts`:

```ts
export async function importRecommendationsCsv(file: File): Promise<RecommendationImportResult> {
  const body = new FormData()
  body.append('file', file)
  const r = await apiFetch('/stock/import', { method: 'POST', body })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
```

`Content-Type` is deliberately not set — the browser must supply the
multipart boundary. `apiFetch` only adds `X-Requested-With`, so this works
as-is.

`Account.tsx` — new prop `onImportRecommendations?: (file: File) => void`,
and a new `<tr>` **between the Export row and the Clear row**, matching
their three-cell shape (spacer / control / help text):

- A hidden `<input type="file" accept=".csv,text/csv">` held by a ref, plus
  an "Import" button (`secondaryButtonClass()`, `w-20`, same as its
  siblings) that clicks the input.
- On `change`: call `onImportRecommendations(file)`, then set
  `input.value = ''` so re-selecting the same file fires `change` again.
- **Not** gated on `hasJudgedItems`, unlike Export and Clear — having no
  judgments is the main reason to import.
- Help text: what the file is, that it merges by newest verdict, and that
  imported items not currently in stock take effect on a later sync.

`App.tsx` — `handleImportRecommendations`, `useCallback` with a
`[setSyncStatus]` dep list so it stays referentially stable and doesn't
defeat `Account`'s `memo()` (see `viewRenderChurn.test.tsx`). Reports
through `setSyncStatus` like the other three handlers rather than
introducing inline state in `Account`:

- `running` → "Cannot import recommendations while a sync or recommendation
  run is in progress".
- otherwise a one-line summary naming imported, updated, unchanged,
  `matched_stock_items` with the "the rest apply as items appear" clause,
  and skipped when non-zero.
- then `getJudgmentStatus()` → `setHasJudgedItems`, so Export/Clear and the
  Store tab's `Recommended` filter (`recommendedAvailable`) enable without
  a reload.

Export's help text updates to the new column list and drops the
"recommended items" framing — it is now every judgment.

## Testing

Backend, `test_recommendations_import.py` (pure parsing, no DB):

- valid file parses; `recommended` accepts each documented spelling and
  casing; `reason` empty → `None`.
- bad `item_key` (wrong length, non-hex, uppercase), unparseable
  `judged_at`, and missing `judged_at` each skip exactly that row, with the
  correct `reader.line_num` and the rest still parsed.
- `judged_at` with trailing `Z` and with a numeric offset both normalize to
  the same naive UTC value (the 3.9/3.10 `fromisoformat` gap).
- duplicate `item_key` in one file collapses to the newest `judged_at` and
  counts as skipped.
- missing required header column raises `InvalidImportError`; row count over
  `MAX_ROWS` raises.
- `errors` is capped at `MAX_REPORTED_ERRORS` while `skipped` is not.

Backend, `test_judgment_crud.py`:

- `get_all_stock_judgments` returns not-recommended rows and owned-item rows
  (both absent from `get_recommended_stock_items`); returns one row per
  judgment when two crawlers share an `item_key`; returns a judgment with no
  `stock_items` row, with blank price/source/link but populated
  artist/title/format from `stock_item_identities`; and blank artist/title
  when no identity row exists either.
- `import_stock_judgments` insert vs update counts; a file row older than
  the local judgment leaves it untouched; equal timestamps leave it
  untouched (strict `>`); an unknown `item_key` inserts.
- Re-importing the same payload twice is a no-op the second time.

Backend, `test_stock_router.py`:

- export emits the 10-column header and a round-trip
  export → import → export is byte-identical.
- import returns the documented counts; returns `running: true` and writes
  nothing while a judgment run is active; `413`/`422` on oversize and on a
  bad header.
- two-user case: user A imports a file containing user B's `item_key`s;
  B's judgments are unchanged and A gains its own rows. This is the
  tenant-isolation assertion.

Frontend, `account.test.tsx`:

- the Import row renders between Export and Clear, and its button is enabled
  when `hasJudgedItems` is false.
- selecting a file calls `onImportRecommendations` with it; the input's
  value is cleared afterwards.
- Export's updated help text renders.

Not tested: real Anthropic calls (unchanged by this slice) and Playwright
paths, per the repo's existing boundaries.

## Runtime/agent document impact

No `.agents/` tree exists in this repo, so `INPUTS.md`, `OUTPUTS.md`, and
`INSTRUCTIONS.md` do not apply.

- `README.md` — updated: the export is now a full judgment ledger and an
  import format, with the caveat that it carries `item_key` values that only
  match instances running the same crawler normalization.
- `docs/superpowers/specs/2026-07-06-store-recommended-filter-design.md` —
  amendment against §6, whose 7-column CSV shape and "matches the
  `Recommended` filter results exactly" acceptance criterion both stop being
  true.
- `CLAUDE.md` — no change; no invariant, layout, or command changes.
- `backend/version.py` — minor bump, per the repo's every-PR rule.
