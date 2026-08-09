# Recommendations Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make paid-for recommendation judgments portable — widen the CSV export into a complete judgment ledger and add an import that loads one back, so a wiped or second instance doesn't re-pay the Anthropic bill.

**Architecture:** `GET /api/stock/export` stops being a shopping list. A new `db.get_all_stock_judgments()` drives *from* `stock_item_judgments`, `LEFT JOIN`ing `stock_item_identities` (durable `item_key → artist/title/format`) for readable columns and a `LEFT JOIN LATERAL` onto `stock_items` for live price/source/link — so judgments for items not currently in stock still export. The file gains `item_key`, `recommended`, and `judged_at` columns and carries every judgment, unfiltered. A new `POST /api/stock/import` parses that file in `backend/recommendations_import.py` (pure, HTTP-free, unit-testable), then upserts via `db.import_stock_judgments()` in one `unnest` statement whose `ON CONFLICT ... WHERE EXCLUDED.judged_at > stock_item_judgments.judged_at` makes newest-wins and idempotency a property of the SQL rather than of application logic. Frontend adds an "Import" row to `Account.tsx`'s Recommendations section between Export and Clear.

**Tech Stack:** FastAPI + psycopg 3 (raw SQL, no ORM), `python-multipart` for the upload, pytest against a real Postgres via `TEST_DATABASE_URL`, React 19 + TypeScript + Vite + Tailwind, vitest + @testing-library/react.

**Spec:** [`docs/specifications/shaping/2026-08-09-recommendations-import-design.md`](../shaping/2026-08-09-recommendations-import-design.md)

**Branch:** `recommendations-import`, worktree at `.worktrees/recommendations-import`, based on `origin/main`. Not stacked on anything.

**Interim-state note:** Task 2 changes the export's on-the-wire shape before Task 7 updates the frontend help text that describes it. Nothing breaks — the frontend just downloads the file and never reads its columns — but between those tasks Account's Export help text is stale. Do not reorder to "fix" this: the parser (Task 3) must exist before the endpoint (Task 5), and the endpoint must exist before the client (Task 6).

**Before starting:** confirm the baseline is green so any later failure is attributable to this plan.

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test pytest
```

```bash
cd frontend && npm test
```

Both must pass. `pytest` needs a running Postgres and `TEST_DATABASE_URL` set — see `README.md`'s environment-variable table. If no Postgres is running:

```bash
docker run -d --name discogs-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
```

```bash
docker exec discogs-pg psql -U postgres -c "CREATE DATABASE discogs_browser_test"
```

Every `pytest` command below assumes `TEST_DATABASE_URL` is exported in your shell; if it isn't, prefix each one as shown above.

---

## Domain background (read before Task 1)

Three facts drive the whole design. Getting any of them wrong produces code that looks right and silently loses data.

**1. `item_key` cannot be recomputed from the exported columns.** `db.compute_item_key(artist, title, url)` is `sha256(f"{artist}|{title}|{url}")`. `replace_stock_items` (`backend/db.py:854`) deliberately feeds it `item["artist"].title()` — the *legacy* casing — while storing `normalize_artist_casing(item["artist"])` in the row. So the artist string in the file will not reproduce the hash. The literal `item_key` must travel in the file, and there is no fallback derivation. Do not add one.

**2. `stock_item_identities` is durable; `stock_items` is not.** Every store sync runs `DELETE FROM stock_items WHERE crawler_id = %s` and reinserts (`backend/db.py:841`). `stock_item_identities` is only ever upserted, never deleted. So at any moment most historical judgments have no live `stock_items` row but *do* have an identity row. That is why export joins both: identities for artist/title/format, stock for price/source/link.

**3. A judgment for an item this instance has never crawled is legal and desirable.** `stock_item_judgments` has a foreign key on `user_id` only — none on `item_key`. Importing such a row means that when a future sync first surfaces the item, `get_unjudged_stock_items` already skips it and it never costs an API call. This is where most of the preserved money lives.

---

## File Structure

**Created — backend:**
- `backend/recommendations_import.py` — CSV parsing, per-row validation, in-file de-duplication, `InvalidImportError`. No DB, no FastAPI imports, so it unit-tests without a database.
- `backend/tests/test_recommendations_import.py` — parser tests, no DB fixtures.

**Modified — backend:**
- `backend/db.py` — `stock_items_item_key_idx` added to `SCHEMA`; new `get_all_stock_judgments()` and `import_stock_judgments()`; new `count_matching_stock_items()`. `get_recommended_stock_items()` is left untouched.
- `backend/routers/stock.py` — `GET /stock/export` widened to 10 columns and re-pointed at the new query; new `POST /stock/import`.
- `backend/version.py` — `"3.4"` → `"3.5"`.
- `backend/tests/test_judgment_crud.py` — export/import db-helper tests.
- `backend/tests/test_stock_router.py` — endpoint tests including round-trip and tenant isolation.

**Modified — frontend:**
- `frontend/src/api/types.ts` — `RecommendationImportResult`.
- `frontend/src/api/client.ts` — `importRecommendationsCsv(file)`.
- `frontend/src/views/Account.tsx` — `onImportRecommendations` prop, Import row between Export and Clear, updated Export help text.
- `frontend/src/App.tsx` — `handleImportRecommendations`.
- `frontend/src/test/account.test.tsx` — Import row tests.

**Modified — docs:**
- `README.md` — new "Recommendations export and import" section (Task 8).
- `docs/superpowers/specs/2026-07-06-store-recommended-filter-design.md` — amendment against §6 (Task 8).

---

## Task 1: Index `stock_items.item_key`

The export's `LEFT JOIN LATERAL` correlates on `s.item_key`, which has no index today — `stock_items` (`backend/db.py:96`) indexes only its `id` primary key. Without this, every exported judgment triggers a sequential scan of the whole stock table.

**Files:**
- Modify: `backend/db.py` (the `SCHEMA` string, after the `listings_item_key_crawler_idx` line at `backend/db.py:150`)

- [ ] **Step 1: Add the index to `SCHEMA`**

In `backend/db.py`, find the end of the `SCHEMA` string:

```python
ALTER TABLE listings ALTER COLUMN release_id DROP NOT NULL;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS item_key TEXT REFERENCES stock_item_identities(item_key);
CREATE UNIQUE INDEX IF NOT EXISTS listings_item_key_crawler_idx ON listings (item_key, crawler_id);
"""
```

Insert before the closing `"""`:

```python
-- stock_items.item_key is not unique (the same artist/title/url can be seen
-- by several crawlers) so this is a plain index, not a unique one. Needed by
-- get_all_stock_judgments' LEFT JOIN LATERAL, and it also serves the
-- existing get_stock_items / get_unjudged_stock_items joins on s.item_key.
CREATE INDEX IF NOT EXISTS stock_items_item_key_idx ON stock_items (item_key);
```

- [ ] **Step 2: Verify the DDL applies cleanly and is idempotent**

Run: `cd backend && python -c "import db; db.init_global_schema(); db.init_global_schema(); print('ok')"` with `DATABASE_URL` pointed at your test database:

```bash
cd backend && DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test python -c "import db; db.init_global_schema(); db.init_global_schema(); print('ok')"
```

Expected: prints `ok` with no error. Running twice proves `IF NOT EXISTS` holds.

- [ ] **Step 3: Confirm the index exists**

```bash
docker exec discogs-pg psql -U postgres -d discogs_browser_test -c "\d stock_items" | grep item_key
```

Expected: a line containing `stock_items_item_key_idx`.

- [ ] **Step 4: Commit**

```bash
git add backend/db.py
git commit -F - <<'MSG'
perf: index stock_items.item_key

Summary:
=======
get_all_stock_judgments' LEFT JOIN LATERAL correlates on s.item_key, which
had no index -- stock_items indexes only its id primary key.

Actions:
=======
- Add stock_items_item_key_idx to SCHEMA as a plain (non-unique) index

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-opus-5
ai-tool: claude-code
ai-surface: claude-code-desktop
ai-executor: local-agent
MSG
```

Every later commit in this plan uses the same trailer block. Per `CLAUDE.md`, commit via `git commit -F` (never `-m`) so the trailers can't be dropped by shell quoting, and fill `ai-model`/`ai-tool`/`ai-surface`/`ai-executor` with the real values for your session rather than copying these verbatim if they differ.

---

## Task 2: `get_all_stock_judgments` — the export query

**Files:**
- Modify: `backend/db.py` (add after `get_recommended_stock_items`, which ends at `backend/db.py:1150`)
- Test: `backend/tests/test_judgment_crud.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_judgment_crud.py`. Note the existing `_seed_stock_item` helper at the top of that file returns the `item_key`, and the `_clean_tables` autouse fixture handles schema init and truncation — do not re-add either.

```python
def test_all_judgments_includes_not_recommended_and_owned_rows(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        yes_key = _seed_stock_item(conn, artist="Artist A", title="Album A", url="https://x/1")
        conn.commit()
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.replace_stock_items(conn, crawler_id, [
            {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
            {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 20.0, "currency": "USD"},
        ])
        conn.commit()
    no_key = db.compute_item_key("Artist B", "Album B", "https://x/2")

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": yes_key, "recommended": True, "reason": "yes please"},
            {"item_key": no_key, "recommended": False, "reason": "no thanks"},
        ])
        conn.commit()
        rows = db.get_all_stock_judgments(conn, alice["id"])

    by_key = {r["item_key"]: r for r in rows}
    assert set(by_key) == {yes_key, no_key}
    assert by_key[no_key]["recommended"] is False
    assert by_key[no_key]["reason"] == "no thanks"
    assert by_key[yes_key]["artist"] == "Artist A"
    assert by_key[yes_key]["source"] == "Amazon"
    assert by_key[yes_key]["price"] == 10.0
    assert by_key[yes_key]["judged_at"] is not None


def test_all_judgments_returns_one_row_per_judgment_when_two_crawlers_share_an_item(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.register_crawler(conn, "Amazon", "/a.py", crawler_type="catalog")
        db.register_crawler(conn, "CCMusic", "/c.py", crawler_type="catalog")
        conn.commit()
        amazon_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        cc_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'CCMusic'").fetchone()["id"]
        item = {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"}
        db.replace_stock_items(conn, amazon_id, [item])
        db.replace_stock_items(conn, cc_id, [item])
        conn.commit()
    item_key = db.compute_item_key("Artist A", "Album A", "https://x/1")

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": item_key, "recommended": True, "reason": "r"},
        ])
        conn.commit()
        rows = db.get_all_stock_judgments(conn, alice["id"])

    assert len(rows) == 1


def test_all_judgments_returns_rows_with_no_live_stock_but_an_identity(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        item_key = _seed_stock_item(conn, artist="Artist A", title="Album A", url="https://x/1")
        conn.commit()
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        # A later sync that no longer carries the item: stock_items rows for
        # this crawler are deleted, stock_item_identities keeps its row.
        db.replace_stock_items(conn, crawler_id, [])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": item_key, "recommended": True, "reason": "r"},
        ])
        conn.commit()
        rows = db.get_all_stock_judgments(conn, alice["id"])

    assert len(rows) == 1
    assert rows[0]["artist"] == "Artist A"
    assert rows[0]["title"] == "Album A"
    assert rows[0]["price"] is None
    assert rows[0]["source"] is None
    assert rows[0]["url"] is None


def test_all_judgments_returns_imported_only_rows_with_blank_artist(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    orphan_key = "a" * 64

    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": orphan_key, "recommended": True, "reason": "from another instance"},
        ])
        conn.commit()
        rows = db.get_all_stock_judgments(conn, alice["id"])

    assert len(rows) == 1
    assert rows[0]["item_key"] == orphan_key
    assert rows[0]["artist"] == ""
    assert rows[0]["title"] == ""
    assert rows[0]["reason"] == "from another instance"


def test_all_judgments_scoped_to_calling_user(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        item_key = _seed_stock_item(conn)
        conn.commit()

    with db.user_scope(bob["id"]) as conn:
        db.upsert_stock_judgments(conn, bob["id"], [
            {"item_key": item_key, "recommended": True, "reason": "bob's"},
        ])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.get_all_stock_judgments(conn, alice["id"]) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_judgment_crud.py -k all_judgments -v
```

Expected: 5 failures, each `AttributeError: module 'db' has no attribute 'get_all_stock_judgments'`.

- [ ] **Step 3: Implement `get_all_stock_judgments`**

Add to `backend/db.py`, immediately after `get_recommended_stock_items`:

```python
def get_all_stock_judgments(conn, user_id: int) -> list[dict]:
    # Drives FROM the judgments table, not from stock_items, so a judgment
    # whose item isn't currently in stock -- or was never crawled here at
    # all, having arrived by import -- still comes out. That's the whole
    # point: the file is a backup of what was paid for, not a shopping list.
    #
    # stock_item_identities is the durable artist/title source (only ever
    # upserted); stock_items is deleted and reinserted per crawler on every
    # sync, so it can only supply the live price/source/link.
    #
    # LEFT JOIN LATERAL ... LIMIT 1 replaces the DISTINCT ON that
    # get_recommended_stock_items needs: item_key is not unique in
    # stock_items, so an unguarded join would emit one row per crawler that
    # saw the item.
    return conn.execute(
        """
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
        """,
        {"user_id": user_id},
    ).fetchall()
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_judgment_crud.py -k all_judgments -v
```

Expected: 5 passed.

- [ ] **Step 5: Run the whole judgment-CRUD file to confirm nothing regressed**

```bash
cd backend && pytest tests/test_judgment_crud.py -v
```

Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_judgment_crud.py
```

Commit subject: `feat: add get_all_stock_judgments for full judgment export`. Body per the Task 1 trailer format.

---

## Task 3: The CSV parser

Pure module, no DB and no FastAPI, so it tests without a Postgres fixture.

**Files:**
- Create: `backend/recommendations_import.py`
- Test: `backend/tests/test_recommendations_import.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_recommendations_import.py`:

```python
from datetime import datetime

import pytest

import recommendations_import as ri

KEY_A = "a" * 64
KEY_B = "b" * 64
HEADER = "artist,title,format,price,source,link,reason,item_key,recommended,judged_at"


def _csv(*rows):
    return "\n".join([HEADER, *rows]) + "\n"


def test_parses_a_valid_row():
    text = _csv(f"Artist A,Album A,LP,10.0,Amazon,https://x/1,looks good,{KEY_A},true,2026-08-09T14:03:22.481923")
    judgments, errors, skipped = ri.parse_judgment_csv(text)
    assert errors == []
    assert skipped == 0
    assert judgments == [{
        "item_key": KEY_A,
        "recommended": True,
        "reason": "looks good",
        "judged_at": datetime(2026, 8, 9, 14, 3, 22, 481923),
    }]


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("TRUE", True), ("True", True), ("t", True), ("yes", True), ("1", True),
    ("false", False), ("FALSE", False), ("False", False), ("f", False), ("no", False), ("0", False),
])
def test_accepts_documented_boolean_spellings(raw, expected):
    text = _csv(f"A,B,,,,,,{KEY_A},{raw},2026-08-09T14:03:22")
    judgments, errors, skipped = ri.parse_judgment_csv(text)
    assert errors == []
    assert judgments[0]["recommended"] is expected


def test_empty_reason_becomes_none():
    text = _csv(f"A,B,,,,,,{KEY_A},true,2026-08-09T14:03:22")
    judgments, _, _ = ri.parse_judgment_csv(text)
    assert judgments[0]["reason"] is None


def test_missing_reason_column_is_tolerated():
    text = "item_key,recommended,judged_at\n" + f"{KEY_A},true,2026-08-09T14:03:22\n"
    judgments, errors, skipped = ri.parse_judgment_csv(text)
    assert errors == []
    assert judgments[0]["reason"] is None


@pytest.mark.parametrize("bad_key", ["", "abc", "A" * 64, "z" * 64, "a" * 63, "a" * 65])
def test_skips_rows_with_a_bad_item_key(bad_key):
    text = _csv(
        f"A,B,,,,,,{bad_key},true,2026-08-09T14:03:22",
        f"C,D,,,,,,{KEY_B},true,2026-08-09T14:03:22",
    )
    judgments, errors, skipped = ri.parse_judgment_csv(text)
    assert [j["item_key"] for j in judgments] == [KEY_B]
    assert skipped == 1
    assert errors[0]["line"] == 2
    assert "item_key" in errors[0]["error"]


def test_skips_rows_with_an_unparseable_or_missing_judged_at():
    text = _csv(
        f"A,B,,,,,,{KEY_A},true,not-a-date",
        f"C,D,,,,,,{KEY_B},true,",
    )
    judgments, errors, skipped = ri.parse_judgment_csv(text)
    assert judgments == []
    assert skipped == 2
    assert [e["line"] for e in errors] == [2, 3]
    assert all("judged_at" in e["error"] for e in errors)


def test_skips_rows_with_an_unrecognized_recommended_value():
    text = _csv(f"A,B,,,,,,{KEY_A},maybe,2026-08-09T14:03:22")
    judgments, errors, skipped = ri.parse_judgment_csv(text)
    assert judgments == []
    assert skipped == 1
    assert "recommended" in errors[0]["error"]


def test_reported_line_numbers_survive_an_embedded_newline():
    text = _csv(
        f'A,B,,,,,"reason\nwith a newline",{KEY_A},true,2026-08-09T14:03:22',
        f"C,D,,,,,,{KEY_B},true,nope",
    )
    _, errors, _ = ri.parse_judgment_csv(text)
    # csv.reader.line_num counts physical lines, so the quoted newline pushes
    # the bad row to line 4, not 3.
    assert errors[0]["line"] == 4


@pytest.mark.parametrize("stamp", ["2026-08-09T14:03:22Z", "2026-08-09T10:03:22-04:00"])
def test_offset_and_z_timestamps_normalize_to_naive_utc(stamp):
    text = _csv(f"A,B,,,,,,{KEY_A},true,{stamp}")
    judgments, errors, _ = ri.parse_judgment_csv(text)
    assert errors == []
    assert judgments[0]["judged_at"] == datetime(2026, 8, 9, 14, 3, 22)
    assert judgments[0]["judged_at"].tzinfo is None


def test_duplicate_item_key_collapses_to_the_newest_and_counts_as_skipped():
    text = _csv(
        f"A,B,,,,,older,{KEY_A},false,2026-08-01T00:00:00",
        f"A,B,,,,,newer,{KEY_A},true,2026-08-09T00:00:00",
    )
    judgments, errors, skipped = ri.parse_judgment_csv(text)
    assert len(judgments) == 1
    assert judgments[0]["reason"] == "newer"
    assert judgments[0]["recommended"] is True
    assert skipped == 1
    assert "duplicate" in errors[0]["error"]


def test_duplicate_kept_row_wins_regardless_of_file_order():
    text = _csv(
        f"A,B,,,,,newer,{KEY_A},true,2026-08-09T00:00:00",
        f"A,B,,,,,older,{KEY_A},false,2026-08-01T00:00:00",
    )
    judgments, _, skipped = ri.parse_judgment_csv(text)
    assert len(judgments) == 1
    assert judgments[0]["reason"] == "newer"
    assert skipped == 1


def test_missing_required_header_column_rejects_the_whole_file():
    text = "artist,title,recommended,judged_at\nA,B,true,2026-08-09T14:03:22\n"
    with pytest.raises(ri.InvalidImportError) as exc:
        ri.parse_judgment_csv(text)
    assert "item_key" in str(exc.value)


def test_empty_file_rejects_as_invalid():
    with pytest.raises(ri.InvalidImportError):
        ri.parse_judgment_csv("")


def test_row_count_over_the_cap_rejects_the_whole_file(monkeypatch):
    monkeypatch.setattr(ri, "MAX_ROWS", 2)
    text = _csv(*[
        f"A,B,,,,,,{format(i, '064x')},true,2026-08-09T14:03:22" for i in range(3)
    ])
    with pytest.raises(ri.InvalidImportError) as exc:
        ri.parse_judgment_csv(text)
    assert "row" in str(exc.value).lower()


def test_error_list_is_capped_but_skipped_count_is_not(monkeypatch):
    monkeypatch.setattr(ri, "MAX_REPORTED_ERRORS", 3)
    text = _csv(*[f"A,B,,,,,,badkey,true,2026-08-09T14:03:22" for _ in range(10)])
    judgments, errors, skipped = ri.parse_judgment_csv(text)
    assert judgments == []
    assert len(errors) == 3
    assert skipped == 10


def test_tolerates_a_utf8_bom_on_the_header():
    text = "﻿" + _csv(f"A,B,,,,,,{KEY_A},true,2026-08-09T14:03:22")
    judgments, errors, _ = ri.parse_judgment_csv(text)
    assert errors == []
    assert judgments[0]["item_key"] == KEY_A
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_recommendations_import.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'recommendations_import'`.

- [ ] **Step 3: Implement the parser**

Create `backend/recommendations_import.py`:

```python
import csv
import io
import re
from datetime import datetime, timezone
from typing import Optional

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ROWS = 100_000
MAX_REPORTED_ERRORS = 10
REQUIRED_COLUMNS = ("item_key", "recommended", "judged_at")

_ITEM_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_TRUE = {"true", "t", "yes", "1"}
_FALSE = {"false", "f", "no", "0"}


class InvalidImportError(Exception):
    """The file is unusable as a whole -- bad header, or too many rows."""


def _parse_recommended(raw: str) -> bool:
    value = (raw or "").strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"recommended must be true or false, got {raw!r}")


def _parse_judged_at(raw: str) -> datetime:
    value = (raw or "").strip()
    if not value:
        raise ValueError("judged_at is required")
    # datetime.fromisoformat only accepts a trailing 'Z' from 3.11 on, and
    # this repo's floor is 3.9, so rewrite it. An offset-aware result is
    # converted to UTC and stripped to naive, matching the column's
    # TIMESTAMP (no time zone) type; a naive value is taken as UTC as-is.
    if value.endswith(("Z", "z")):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"judged_at is not an ISO-8601 timestamp: {raw!r}")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def parse_judgment_csv(text: str) -> tuple[list[dict], list[dict], int]:
    """Parse an exported judgment ledger.

    Returns (judgments, errors, skipped). Best-effort per row: an unparseable
    row is skipped and counted, the rest still parse. Raises
    InvalidImportError only for whole-file problems, where a per-row error
    list would be one entry long per row and tell the user nothing.
    """
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames:
        raise InvalidImportError("File is empty or has no header row.")
    missing = [c for c in REQUIRED_COLUMNS if c not in reader.fieldnames]
    if missing:
        raise InvalidImportError(f"Missing required column(s): {', '.join(missing)}.")

    by_key: dict[str, dict] = {}
    errors: list[dict] = []
    skipped = 0
    seen = 0

    def _fail(line: int, message: str):
        nonlocal skipped
        skipped += 1
        if len(errors) < MAX_REPORTED_ERRORS:
            errors.append({"line": line, "error": message})

    for row in reader:
        seen += 1
        if seen > MAX_ROWS:
            raise InvalidImportError(f"File has more than {MAX_ROWS} rows.")
        line = reader.line_num
        item_key = (row.get("item_key") or "").strip()
        if not _ITEM_KEY_RE.match(item_key):
            _fail(line, "item_key must be 64 lowercase hex characters")
            continue
        try:
            recommended = _parse_recommended(row.get("recommended"))
            judged_at = _parse_judged_at(row.get("judged_at"))
        except ValueError as e:
            _fail(line, str(e))
            continue
        reason: Optional[str] = (row.get("reason") or "").strip() or None
        candidate = {
            "item_key": item_key,
            "recommended": recommended,
            "reason": reason,
            "judged_at": judged_at,
        }
        # Postgres raises "ON CONFLICT DO UPDATE command cannot affect row a
        # second time" if one statement presents the same conflict target
        # twice, so a duplicated key has to be collapsed here rather than
        # left for the upsert -- otherwise one duplicate fails the whole
        # import. Newest judged_at wins, matching the upsert's own rule.
        existing = by_key.get(item_key)
        if existing is None:
            by_key[item_key] = candidate
            continue
        _fail(line, f"duplicate item_key {item_key}, keeping the newest judged_at")
        if candidate["judged_at"] > existing["judged_at"]:
            by_key[item_key] = candidate

    return list(by_key.values()), errors, skipped
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_recommendations_import.py -v
```

Expected: all passed (the parametrized cases expand to ~30 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/recommendations_import.py backend/tests/test_recommendations_import.py
```

Commit subject: `feat: add judgment-CSV parser with per-row validation`.

---

## Task 4: `import_stock_judgments` and `count_matching_stock_items`

**Files:**
- Modify: `backend/db.py` (add after `get_all_stock_judgments` from Task 2)
- Test: `backend/tests/test_judgment_crud.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_judgment_crud.py`. Add `from datetime import datetime` to that file's imports if it isn't there yet (it currently imports only `pytest` and `db`).

```python
def test_import_counts_inserts_and_updates_separately(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        imported, updated = db.import_stock_judgments(conn, alice["id"], [
            {"item_key": "a" * 64, "recommended": True, "reason": "r1",
             "judged_at": datetime(2026, 8, 1)},
            {"item_key": "b" * 64, "recommended": False, "reason": "r2",
             "judged_at": datetime(2026, 8, 1)},
        ])
        conn.commit()
        assert (imported, updated) == (2, 0)

        imported, updated = db.import_stock_judgments(conn, alice["id"], [
            {"item_key": "a" * 64, "recommended": False, "reason": "newer",
             "judged_at": datetime(2026, 8, 9)},
        ])
        conn.commit()
        assert (imported, updated) == (0, 1)
        row = conn.execute(
            "SELECT recommended, reason, judged_at FROM stock_item_judgments WHERE item_key = %s",
            ["a" * 64],
        ).fetchone()
    assert row["recommended"] is False
    assert row["reason"] == "newer"
    assert row["judged_at"] == datetime(2026, 8, 9)


def test_import_preserves_the_files_judged_at_rather_than_stamping_now(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.import_stock_judgments(conn, alice["id"], [
            {"item_key": "a" * 64, "recommended": True, "reason": None,
             "judged_at": datetime(2020, 1, 2, 3, 4, 5)},
        ])
        conn.commit()
        row = conn.execute(
            "SELECT judged_at FROM stock_item_judgments WHERE item_key = %s", ["a" * 64]
        ).fetchone()
    assert row["judged_at"] == datetime(2020, 1, 2, 3, 4, 5)


def test_import_leaves_a_newer_local_judgment_untouched(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        db.import_stock_judgments(conn, alice["id"], [
            {"item_key": "a" * 64, "recommended": True, "reason": "local",
             "judged_at": datetime(2026, 8, 9)},
        ])
        conn.commit()

        imported, updated = db.import_stock_judgments(conn, alice["id"], [
            {"item_key": "a" * 64, "recommended": False, "reason": "older file",
             "judged_at": datetime(2026, 8, 1)},
        ])
        conn.commit()
        assert (imported, updated) == (0, 0)
        row = conn.execute(
            "SELECT recommended, reason FROM stock_item_judgments WHERE item_key = %s",
            ["a" * 64],
        ).fetchone()
    assert row["recommended"] is True
    assert row["reason"] == "local"


def test_import_of_an_identical_timestamp_is_a_no_op(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    payload = [{"item_key": "a" * 64, "recommended": True, "reason": "r",
                "judged_at": datetime(2026, 8, 9)}]

    with db.user_scope(alice["id"]) as conn:
        assert db.import_stock_judgments(conn, alice["id"], payload) == (1, 0)
        conn.commit()
        assert db.import_stock_judgments(conn, alice["id"], payload) == (0, 0)
        conn.commit()


def test_import_of_an_empty_payload_is_a_no_op(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    with db.user_scope(alice["id"]) as conn:
        assert db.import_stock_judgments(conn, alice["id"], []) == (0, 0)


def test_import_does_not_touch_another_users_judgment_for_the_same_key(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        conn.commit()
    shared_key = "a" * 64

    with db.user_scope(bob["id"]) as conn:
        db.import_stock_judgments(conn, bob["id"], [
            {"item_key": shared_key, "recommended": True, "reason": "bob's",
             "judged_at": datetime(2026, 8, 1)},
        ])
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.import_stock_judgments(conn, alice["id"], [
            {"item_key": shared_key, "recommended": False, "reason": "alice's",
             "judged_at": datetime(2026, 8, 9)},
        ]) == (1, 0)
        conn.commit()

    with db.get_admin_pool().connection() as conn:
        rows = conn.execute(
            "SELECT user_id, reason FROM stock_item_judgments WHERE item_key = %s ORDER BY user_id",
            [shared_key],
        ).fetchall()
    assert [(r["user_id"], r["reason"]) for r in rows] == [
        (alice["id"], "alice's"), (bob["id"], "bob's"),
    ]


def test_count_matching_stock_items_counts_only_keys_present_in_stock(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        item_key = _seed_stock_item(conn)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.count_matching_stock_items(conn, [item_key, "a" * 64]) == 1
        assert db.count_matching_stock_items(conn, ["a" * 64]) == 0
        assert db.count_matching_stock_items(conn, []) == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_judgment_crud.py -k "import_ or count_matching" -v
```

Expected: failures with `AttributeError: module 'db' has no attribute 'import_stock_judgments'`.

- [ ] **Step 3: Implement both helpers**

Add to `backend/db.py`, after `get_all_stock_judgments`:

```python
def import_stock_judgments(conn, user_id: int, judgments: list[dict]) -> tuple[int, int]:
    """Upsert imported judgments, newest judged_at winning. Returns
    (inserted, updated); rows whose local judgment is already at least as new
    are neither, and are the caller's `unchanged`.

    Deliberately not upsert_stock_judgments: that one stamps
    judged_at = CURRENT_TIMESTAMP, which would erase the imported timestamps,
    break newest-wins on the next round-trip, and make every imported row
    look freshly judged.

    Callers must have collapsed duplicate item_keys first -- Postgres rejects
    a statement whose ON CONFLICT target appears twice.
    """
    if not judgments:
        return (0, 0)
    rows = conn.execute(
        """
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
        """,
        {
            "user_id": user_id,
            "keys": [j["item_key"] for j in judgments],
            "recommended": [j["recommended"] for j in judgments],
            "reasons": [j.get("reason") for j in judgments],
            "judged_at": [j["judged_at"] for j in judgments],
        },
    ).fetchall()
    # xmax = 0 marks a row this statement inserted rather than updated.
    inserted = sum(1 for r in rows if r["inserted"])
    return (inserted, len(rows) - inserted)


def count_matching_stock_items(conn, item_keys: list[str]) -> int:
    """How many of these keys are in stock right now. The Recommended filter
    inner-joins stock_items, so this is what decides whether an import
    changes anything the user can see yet.
    """
    if not item_keys:
        return 0
    return conn.execute(
        "SELECT COUNT(DISTINCT item_key) FROM stock_items WHERE item_key = ANY(%(keys)s)",
        {"keys": item_keys},
    ).fetchone()["count"]
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_judgment_crud.py -k "import_ or count_matching" -v
```

Expected: all passed.

- [ ] **Step 5: Run the whole backend suite**

```bash
cd backend && pytest
```

Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_judgment_crud.py
```

Commit subject: `feat: add newest-wins judgment import upsert`.

---

## Task 5: The endpoints

**Files:**
- Modify: `backend/routers/stock.py:104-118` (rewrite `export_recommended_stock`, add `import_recommendations`)
- Test: `backend/tests/test_stock_router.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_stock_router.py`. The `authed_client_factory` fixture at the top of that file already wires `stock_router.router` behind `AuthMiddleware`; `X-Requested-With` is required on non-GET requests by that middleware, so every POST below sets it.

```python
def _seed_judged_item(user_id, artist="Artist A", title="Album A", url="https://x/1"):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py", crawler_type="catalog")
        conn.commit()
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.replace_stock_items(conn, crawler_id, [
            {"artist": artist, "title": title, "url": url, "price": 10.0, "currency": "USD"},
        ])
        conn.commit()
    item_key = db.compute_item_key(artist.title(), title, url)
    with db.user_scope(user_id) as conn:
        db.upsert_stock_judgments(conn, user_id, [
            {"item_key": item_key, "recommended": True, "reason": "yes"},
        ])
        conn.commit()
    return item_key


def test_export_emits_the_ten_column_header_and_not_recommended_rows(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    yes_key = _seed_judged_item(alice["id"])
    with db.user_scope(alice["id"]) as conn:
        db.upsert_stock_judgments(conn, alice["id"], [
            {"item_key": "b" * 64, "recommended": False, "reason": "no"},
        ])
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.get("/api/stock/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0] == "artist,title,format,price,source,link,reason,item_key,recommended,judged_at"
    assert any(",true," in ln and yes_key in ln for ln in lines[1:])
    assert any(",false," in ln and "b" * 64 in ln for ln in lines[1:])


def test_export_import_export_round_trips_byte_identically(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    _seed_judged_item(alice["id"])
    client = authed_client_factory(alice["id"])

    first = client.get("/api/stock/export").text
    r = client.post(
        "/api/stock/import",
        files={"file": ("recommendations.csv", first, "text/csv")},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 200
    body = r.json()
    # Same instance, same timestamps: strict > means nothing applies.
    assert (body["imported"], body["updated"]) == (0, 0)
    assert body["unchanged"] == 1
    assert client.get("/api/stock/export").text == first


def test_import_reports_counts_and_stock_matches(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    in_stock_key = _seed_judged_item(alice["id"])
    with db.user_scope(alice["id"]) as conn:
        db.clear_stock_judgments(conn, alice["id"])
        conn.commit()

    header = "artist,title,format,price,source,link,reason,item_key,recommended,judged_at"
    csv_text = "\n".join([
        header,
        f"A,B,,,,,in stock,{in_stock_key},true,2026-08-09T00:00:00",
        f"C,D,,,,,never seen here,{'c' * 64},false,2026-08-09T00:00:00",
        f"E,F,,,,,bad key,nothex,true,2026-08-09T00:00:00",
    ]) + "\n"

    client = authed_client_factory(alice["id"])
    r = client.post(
        "/api/stock/import",
        files={"file": ("recommendations.csv", csv_text, "text/csv")},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 200
    assert r.json() == {
        "imported": 2, "updated": 0, "unchanged": 0, "skipped": 1,
        "errors": [{"line": 4, "error": "item_key must be 64 lowercase hex characters"}],
        "matched_stock_items": 1, "running": False,
    }


def test_import_rejects_a_bad_header_with_422(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(alice["id"])

    r = client.post(
        "/api/stock/import",
        files={"file": ("x.csv", "artist,title\nA,B\n", "text/csv")},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 422
    assert "item_key" in r.json()["detail"]


def test_import_rejects_an_oversized_body_with_413(pg_test_db, authed_client_factory, monkeypatch):
    import recommendations_import as ri

    monkeypatch.setattr(ri, "MAX_UPLOAD_BYTES", 32)
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(alice["id"])

    r = client.post(
        "/api/stock/import",
        files={"file": ("x.csv", "a" * 200, "text/csv")},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 413


def test_import_refuses_while_a_judgment_run_is_active(pg_test_db, authed_client_factory, monkeypatch):
    # judgment_running is faked rather than driven for real, mirroring the
    # rationale on test_stock_judge_start_returns_false_when_already_running_for_calling_user
    # above: a bare TestClient opens its own event loop per request, so a real
    # asyncio.Task can't be observed across requests here.
    monkeypatch.setattr(crawl_manager, "judgment_running", lambda uid: True)
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(alice["id"])

    header = "artist,title,format,price,source,link,reason,item_key,recommended,judged_at"
    csv_text = f"{header}\nA,B,,,,,r,{'a' * 64},true,2026-08-09T00:00:00\n"
    r = client.post(
        "/api/stock/import",
        files={"file": ("x.csv", csv_text, "text/csv")},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 200
    assert r.json()["running"] is True
    assert r.json()["imported"] == 0
    with db.user_scope(alice["id"]) as conn:
        assert db.has_any_stock_judgment(conn, alice["id"]) is False


def test_import_does_not_write_another_users_judgments(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        conn.commit()
    shared_key = "a" * 64
    with db.user_scope(bob["id"]) as conn:
        db.upsert_stock_judgments(conn, bob["id"], [
            {"item_key": shared_key, "recommended": True, "reason": "bob's"},
        ])
        conn.commit()

    header = "artist,title,format,price,source,link,reason,item_key,recommended,judged_at"
    csv_text = f"{header}\nA,B,,,,,alice's,{shared_key},false,2036-01-01T00:00:00\n"
    r = authed_client_factory(alice["id"]).post(
        "/api/stock/import",
        files={"file": ("x.csv", csv_text, "text/csv")},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 200
    assert r.json()["imported"] == 1

    with db.get_admin_pool().connection() as conn:
        rows = conn.execute(
            "SELECT user_id, reason FROM stock_item_judgments WHERE item_key = %s ORDER BY user_id",
            [shared_key],
        ).fetchall()
    assert [(r["user_id"], r["reason"]) for r in rows] == [
        (alice["id"], "alice's"), (bob["id"], "bob's"),
    ]
```

`test_import_reports_counts_and_stock_matches` asserts the exact error string from Task 3's parser. If you changed that wording, change it here too — don't loosen the assertion.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_stock_router.py -k "export or import" -v
```

Expected: the export test fails on the header assertion (still 7 columns); every import test fails with 404 or 405, since `/api/stock/import` doesn't exist.

- [ ] **Step 3: Rewrite the export endpoint**

In `backend/routers/stock.py`, replace the whole `export_recommended_stock` function (`backend/routers/stock.py:104-118`) with:

```python
EXPORT_COLUMNS = [
    "artist", "title", "format", "price", "source", "link", "reason",
    "item_key", "recommended", "judged_at",
]


@router.get("/stock/export")
def export_stock_judgments(request: Request):
    user_id = request.state.user_id
    with db.user_scope(user_id) as conn:
        rows = db.get_all_stock_judgments(conn, user_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_COLUMNS)
    for row in rows:
        writer.writerow([
            row["artist"], row["title"], row["format"], row["price"],
            row["source"], row["url"], row["reason"], row["item_key"],
            # An explicit lowercase literal: csv.writer would render the
            # boolean as Python's "True"/"False", which is not the documented
            # format even though the importer would still accept it.
            "true" if row["recommended"] else "false",
            row["judged_at"].isoformat(),
        ])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=recommendations.csv"},
    )
```

- [ ] **Step 4: Add the import endpoint**

Still in `backend/routers/stock.py`, add after the export endpoint:

```python
@router.post("/stock/import")
async def import_stock_judgments_endpoint(request: Request, file: UploadFile = File(...)):
    user_id = request.state.user_id
    empty = {
        "imported": 0, "updated": 0, "unchanged": 0, "skipped": 0,
        "errors": [], "matched_stock_items": 0,
    }
    # A concurrent judgment run would race this upsert on the same rows.
    # Mirrors clear_stock_judgment's guard, including its 200-with-a-flag
    # shape rather than an error status.
    if crawl_manager.judgment_running(user_id) or crawl_manager.stock_sync_running:
        return {**empty, "running": True}

    # Read cap+1, not the whole body, so an oversized upload isn't buffered
    # in full -- same pattern as upload_avatar in routers/session.py.
    data = await file.read(recommendations_import.MAX_UPLOAD_BYTES + 1)
    if len(data) > recommendations_import.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is larger than {recommendations_import.MAX_UPLOAD_BYTES} bytes.",
        )
    # utf-8-sig strips a BOM that spreadsheet round-trips add; errors are
    # replaced rather than fatal so one bad byte doesn't reject the file.
    text = data.decode("utf-8-sig", errors="replace")

    try:
        judgments, errors, skipped = recommendations_import.parse_judgment_csv(text)
    except recommendations_import.InvalidImportError as e:
        raise HTTPException(status_code=422, detail=str(e))

    with db.user_scope(user_id) as conn:
        imported, updated = db.import_stock_judgments(conn, user_id, judgments)
        matched = db.count_matching_stock_items(conn, [j["item_key"] for j in judgments])
        conn.commit()

    return {
        "imported": imported,
        "updated": updated,
        "unchanged": len(judgments) - imported - updated,
        "skipped": skipped,
        "errors": errors,
        "matched_stock_items": matched,
        "running": False,
    }
```

Update the file's imports at `backend/routers/stock.py:1-8`:

```python
import csv
import io
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel
from typing import Optional
import db
import recommendations_import
from admin import require_admin
from crawl_manager import crawl_manager
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_stock_router.py -k "export or import" -v
```

Expected: all passed.

- [ ] **Step 6: Run the whole backend suite**

```bash
cd backend && pytest
```

Expected: all passed. If an existing test asserted the old 7-column export, it is now wrong and must be updated to the 10-column shape — not deleted.

- [ ] **Step 7: Commit**

```bash
git add backend/routers/stock.py backend/tests/test_stock_router.py
```

Commit subject: `feat: widen the judgment export and add POST /api/stock/import`.

---

## Task 6: Frontend API client

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts:234-238`
- Test: `frontend/src/test/client.test.ts`

- [ ] **Step 1: Write the failing test**

Append inside the existing `describe('crawl/user-settings client functions', ...)` block in `frontend/src/test/client.test.ts`. That block's `beforeEach` already provides the `fetchMock` used below, and the file's convention is a plain object resolution (`{ ok: true, json: async () => ... }`) rather than a real `Response` — match it:

```ts
  it('importRecommendationsCsv posts the file as multipart without setting Content-Type', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        imported: 2, updated: 0, unchanged: 0, skipped: 0,
        errors: [], matched_stock_items: 1, running: false,
      }),
    })
    const file = new File(['artist,title\n'], 'recommendations.csv', { type: 'text/csv' })

    const result = await importRecommendationsCsv(file)

    expect(result.imported).toBe(2)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toContain('/stock/import')
    expect(init.method).toBe('POST')
    expect(init.body).toBeInstanceOf(FormData)
    expect((init.body as FormData).get('file')).toBe(file)
    // The browser must supply the multipart boundary; setting Content-Type by
    // hand omits it and the request fails to parse server-side.
    expect(new Headers(init.headers).has('Content-Type')).toBe(false)
  })
```

Add `importRecommendationsCsv` to that file's existing import list from `../api/client` (`frontend/src/test/client.test.ts:2`).

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd frontend && npx vitest run src/test/client.test.ts -t 'multipart'
```

Expected: FAIL — `importRecommendationsCsv is not exported by src/api/client.ts`.

- [ ] **Step 3: Add the type**

In `frontend/src/api/types.ts`, add:

```ts
export interface RecommendationImportError {
  line: number
  error: string
}

export interface RecommendationImportResult {
  imported: number
  updated: number
  unchanged: number
  skipped: number
  errors: RecommendationImportError[]
  matched_stock_items: number
  running: boolean
}
```

- [ ] **Step 4: Add the client function**

In `frontend/src/api/client.ts`, add `RecommendationImportResult` to the type import at the top of the file, then add immediately after `exportRecommendationsCsv` (`frontend/src/api/client.ts:234-238`):

```ts
export async function importRecommendationsCsv(file: File): Promise<RecommendationImportResult> {
  const body = new FormData()
  body.append('file', file)
  // Content-Type is deliberately unset: the browser adds it with the
  // multipart boundary, which a hand-set header would omit.
  const r = await apiFetch('/stock/import', { method: 'POST', body })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd frontend && npx vitest run src/test/client.test.ts
```

Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/test/client.test.ts
```

Commit subject: `feat: add importRecommendationsCsv client function`.

---

## Task 7: The Import row in Account, and the App handler

**Files:**
- Modify: `frontend/src/views/Account.tsx` (props block at `frontend/src/views/Account.tsx:7-28`; the Export and Clear rows at `frontend/src/views/Account.tsx:281-311`)
- Modify: `frontend/src/App.tsx` (handler near `frontend/src/App.tsx:353-382`; props passed at `frontend/src/App.tsx:536-539`)
- Test: `frontend/src/test/account.test.tsx`

- [ ] **Step 1: Write the failing tests**

Append inside the existing `describe('Account', ...)` block in `frontend/src/test/account.test.tsx`:

```tsx
it('renders Import between Export and Clear', async () => {
  render(<Account avatarVersion={0} onAvatarChange={() => {}} hasJudgedItems={true} />)
  await screen.findByRole('button', { name: 'Export' })
  const labels = screen.getAllByRole('button')
    .map((b) => b.textContent)
    .filter((t) => t === 'Refresh' || t === 'Export' || t === 'Import' || t === 'Clear')
  expect(labels).toEqual(['Refresh', 'Export', 'Import', 'Clear'])
})

it('enables Import even when nothing has been judged yet', () => {
  render(<Account avatarVersion={0} onAvatarChange={() => {}} hasJudgedItems={false} />)
  expect(screen.getByRole('button', { name: 'Import' })).toBeEnabled()
})

it('passes the selected file to onImportRecommendations and clears the input', async () => {
  const onImportRecommendations = vi.fn()
  render(
    <Account
      avatarVersion={0}
      onAvatarChange={() => {}}
      onImportRecommendations={onImportRecommendations}
    />,
  )
  const file = new File(['artist,title\n'], 'recommendations.csv', { type: 'text/csv' })
  const input = screen.getByTestId('recommendations-import-input') as HTMLInputElement
  fireEvent.change(input, { target: { files: [file] } })
  await waitFor(() => expect(onImportRecommendations).toHaveBeenCalledWith(file))
  // Cleared so re-picking the same file fires change again.
  expect(input.value).toBe('')
})
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd frontend && npx vitest run src/test/account.test.tsx -t Import
```

Expected: FAIL — no button named `Import`, and `recommendations-import-input` not found.

- [ ] **Step 3: Add the prop and the ref to `Account.tsx`**

In the `Props` interface, after `onExportRecommendations`:

```tsx
  onImportRecommendations?: (file: File) => void
```

In the destructured parameter list, after `onExportRecommendations = () => {},`:

```tsx
  onImportRecommendations = () => {},
```

With the other refs near `frontend/src/views/Account.tsx:30`:

```tsx
  const importInputRef = useRef<HTMLInputElement>(null)
```

Above the `return`, with the other handlers:

```tsx
  const handleImportFileSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    // Reset before handing the file off, so selecting the same file twice in
    // a row still fires a change event.
    e.target.value = ''
    if (file) onImportRecommendations(file)
  }
```

- [ ] **Step 4: Add the Import row between Export and Clear**

In the Recommendations `<table>`, insert a new `<tr>` after the Export row (the one whose button calls `onExportRecommendations`, `frontend/src/views/Account.tsx:281-295`) and before the Clear row:

```tsx
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left align-top whitespace-nowrap w-40"></td>
              <td className="py-3 pr-4 text-left align-top">
                <input
                  ref={importInputRef}
                  data-testid="recommendations-import-input"
                  type="file"
                  accept=".csv,text/csv"
                  className="hidden"
                  onChange={handleImportFileSelected}
                />
                <button
                  onClick={() => importInputRef.current?.click()}
                  className={`w-20 text-center px-3 py-1 text-xs disabled:opacity-50 ${secondaryButtonClass()}`}
                >
                  Import
                </button>
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Load a recommendations CSV exported from this or another instance, so judgments
                you already paid for aren't re-evaluated. Each item keeps whichever verdict was
                judged most recently. Imported items that aren't in stock right now take effect
                the next time a Store sync sees them. Judgments reflect the taste of the
                collection they were made against, so a file from someone else carries their
                preferences, not yours.
              </td>
            </tr>
```

Note the Import button is not `disabled={!hasJudgedItems}`, unlike Export and Clear — having no judgments is the main reason to import.

- [ ] **Step 5: Update the Export help text, which now describes the wrong file**

Replace the Export row's help-text cell content (`frontend/src/views/Account.tsx:294`):

```tsx
                Download every judgment — recommended and not — as CSV (artist, title, format,
                price, source, link, reason, item_key, recommended, judged_at). Keep it as a
                backup: it can be imported here or into another instance without paying to
                re-evaluate.
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd frontend && npx vitest run src/test/account.test.tsx
```

Expected: all passed, including the pre-existing Export tests.

- [ ] **Step 7: Wire the handler in `App.tsx`**

Add `importRecommendationsCsv` to the `./api/client` import at `frontend/src/App.tsx:11`. Add the handler after `handleExportRecommendations` (`frontend/src/App.tsx:353-365`):

```tsx
  const handleImportRecommendations = useCallback(async (file: File) => {
    try {
      const r = await importRecommendationsCsv(file)
      if (r.running) {
        setSyncStatus('Cannot import recommendations while a sync or recommendation run is in progress')
        return
      }
      const applied = r.imported + r.updated
      const parts = [
        `Imported ${applied} judgment${applied === 1 ? '' : 's'}`,
        `${r.unchanged} already current`,
        `${r.matched_stock_items} match items in stock now — the rest apply as items appear`,
      ]
      if (r.skipped > 0) parts.push(`${r.skipped} row${r.skipped === 1 ? '' : 's'} skipped`)
      setSyncStatus(`${parts.join('; ')}.`)
      const status = await getJudgmentStatus()
      setHasJudgedItems(status.any_judged)
    } catch (e: any) {
      setSyncStatus(`Import recommendations failed: ${e.message}`)
    }
  }, [setSyncStatus])
```

`useCallback` with a `[setSyncStatus]` dep list is load-bearing: `Account` is wrapped in `memo()`, and an unstable prop would re-render it on every crawl SSE event. `viewRenderChurn.test.tsx` asserts this.

Pass it through where the other three are passed (`frontend/src/App.tsx:536-539`):

```tsx
            onImportRecommendations={handleImportRecommendations}
```

- [ ] **Step 8: Run the whole frontend suite**

```bash
cd frontend && npm test
```

Expected: all passed, `viewRenderChurn.test.tsx` included.

- [ ] **Step 9: Typecheck and build**

```bash
cd frontend && npx tsc --noEmit && npm run build
```

Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/views/Account.tsx frontend/src/App.tsx frontend/src/test/account.test.tsx
```

Commit subject: `feat: add recommendations Import between Export and Clear`.

---

## Task 8: Docs, spec-drift amendment, version bump

Folded here rather than left to a trailing catch-all: these are the docs whose deliverables Tasks 2, 5, and 7 changed.

**Files:**
- Modify: `README.md` (new section between "Authentication" ending at `README.md:60` and "Deployment (Synology NAS)" at `README.md:61`)
- Modify: `docs/superpowers/specs/2026-07-06-store-recommended-filter-design.md` (§6 "Export Recommendations action", `docs/superpowers/specs/2026-07-06-store-recommended-filter-design.md:389-415`)
- Modify: `backend/version.py`

- [ ] **Step 1: Add the README section**

`README.md` documents observable behavior and there is now a new user-facing facility plus a changed file format. Insert before `## Deployment (Synology NAS)`:

```markdown
## Recommendations export and import

Recommendation judgments are produced by Claude and cost money per item, so
they're exportable and importable — Profile → Recommendations → Export /
Import.

The export (`recommendations.csv`) is a complete ledger, not a shopping
list: every judgment, recommended and not, including items you already own.
Withholding the not-recommended verdicts would be false economy — a "no"
costs exactly as much to obtain as a "yes", and it's what stops an item
being re-evaluated on the next run.

Import merges by `item_key`, keeping whichever verdict has the later
`judged_at`, so re-importing the same file changes nothing and importing
from a more current instance wins. Rows that fail validation are skipped and
reported by line number; the rest still import.

Two things to know:

- `item_key` is a hash of the store listing's artist, title, and URL, and it
  cannot be recomputed from the file's other columns. A file only matches an
  instance running the same crawlers against the same stores. The response's
  "match items in stock now" count is how you tell whether it lined up.
- Judgments are relative to the collection and wantlist they were judged
  against. A file from someone else imports their taste, not just their
  spend.

Imported verdicts for items not currently in stock are kept, and take effect
the next time a Store sync surfaces the item — so a fresh instance can
import thousands of judgments and see no immediate change in the Recommended
filter. That's expected.
```

- [ ] **Step 2: Amend the drifted spec**

§6 of `docs/superpowers/specs/2026-07-06-store-recommended-filter-design.md` specifies the 7-column shape and an acceptance criterion that no longer holds. Append to that section, matching the `**Amendment (date, branch):**` convention already used throughout that file:

```markdown
**Amendment (2026-08-09, branch `recommendations-import`):** this section's
CSV shape and its "rows exactly match the current `Recommended` filter
results" acceptance criterion no longer hold. `GET /api/stock/export` now
emits ten columns —
`artist,title,format,price,source,link,reason,item_key,recommended,judged_at`
— and every judgment for the calling user, recommended and not, with no
`_not_owned_clause` filter. It is a portable backup consumed by the new
`POST /api/stock/import`, not a view of the Recommended tab. The query moved
from `get_recommended_stock_items` (unchanged, still used elsewhere) to
`get_all_stock_judgments`, which drives from `stock_item_judgments` so
judgments with no live stock row still export. See
[`2026-08-09-recommendations-import-design.md`](../../specifications/shaping/2026-08-09-recommendations-import-design.md).
```

- [ ] **Step 3: Bump the version**

`backend/version.py`: `VERSION = "3.4"` → `VERSION = "3.5"`. Minor bump is the default per `CLAUDE.md`; do not take a major bump.

- [ ] **Step 4: Run the pre-PR spec-drift check across both spec trees**

`CLAUDE.md` requires this on every branch, against every spec — not just the one this feature owns.

```bash
grep -rln "stock/export\|recommendations.csv\|get_recommended_stock_items\|Export Recommendations" docs/superpowers/specs/ docs/specifications/shaping/
```

For each file returned, confirm its text still describes what shipped. Amend any that drifted as its own commit. Record what you found — including "none beyond the amendment in Step 2" — for the PR description.

- [ ] **Step 5: Run both suites one final time**

```bash
cd backend && pytest
```

```bash
cd frontend && npm test
```

Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/superpowers/specs/2026-07-06-store-recommended-filter-design.md backend/version.py
```

Commit subject: `docs: document recommendations import, amend the drifted export spec`.

---

## Manual verification before opening the PR

Automated tests don't cover the browser round-trip (file picker, multipart boundary, download), and `CLAUDE.md` treats integration testing as manual.

- [ ] Start both services: `make dev`, open http://localhost:5173.
- [ ] Profile → Recommendations → Export. Confirm the download has 10 columns, contains `false` rows, and that `judged_at` is populated.
- [ ] Profile → Recommendations → Import, pick the file you just downloaded. Confirm the status bar reports `Imported 0 judgments; N already current; ...` — a same-instance re-import must be a no-op.
- [ ] Hand-edit the file: flip one row's `recommended`, set its `judged_at` to a future date, and corrupt another row's `item_key`. Re-import. Confirm the flipped verdict applied, the corrupt row was skipped with its line number, and the Store tab's Recommended filter reflects the flip.
- [ ] Pick the same file twice in a row without reloading, to confirm the input reset works.

---

## Documentation impact summary

For the PR description:

- `README.md` — new "Recommendations export and import" section (Task 8, Step 1).
- `docs/superpowers/specs/2026-07-06-store-recommended-filter-design.md` — §6 amendment (Task 8, Step 2).
- `CLAUDE.md` — no change. No invariant, repo-layout, golden-command, or hard-rule change: the data directory, crawler interface, and crawl-queue invariants are all untouched.
- No `.agents/` tree exists in this repo, so `INPUTS.md`, `OUTPUTS.md`, and `INSTRUCTIONS.md` do not apply. Were one added later, `POST /api/stock/import` would belong in `INPUTS.md` as a new API-caller trigger.
- `backend/version.py` — `"3.4"` → `"3.5"` (Task 8, Step 3).
