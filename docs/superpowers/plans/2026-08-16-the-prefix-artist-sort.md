# "The"-prefix artist sort Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bands whose name starts with "The" sort as if that word were moved to the end ("The Beatles" sorts under B, next to "Beatles") in the Collection, Wantlist, Store, and Track tabs — row list and artist sidebar both — without changing any displayed text.

**Architecture:** Two tiny helpers in `backend/db.py`, `_artist_sort_sql(column)` (a SQL `CASE` fragment) and `_artist_sort_key(name)` (its Python equivalent), sharing one `"the "` literal. They replace the existing `LOWER(...)` sort expressions at the three places artist ordering happens today: `get_library_releases` (SQL, Collection/Wantlist row list), `get_stock_items` (SQL, Store/Track row list), and `_canonical_artist_list` (Python, the artist-filter sidebar shared by all four tabs).

**Tech Stack:** Python 3.9, `psycopg` (raw SQL via `conn.execute`), `pytest`/`pytest-asyncio` against a real Postgres test database.

## Global Constraints

- Python ≥3.9 — no `str | None`; use `Optional[str]`.
- No comments unless the WHY is non-obvious.
- Sort key strips only the literal word "The" (case-insensitive), never "A"/"An", and never touches displayed artist text.
- `LIKE 'the %'` (a following word required) is the exact match rule — it deliberately excludes a bare artist named "The" and names like "Theatre of Hate".
- Tests run with `TEST_DATABASE_URL`, `IDENTITY_DB_PASSWORD`, `APP_DB_PASSWORD` all set (see project `CLAUDE.md`, "Tests"); run pytest in the foreground, not backgrounded, and never two suites concurrently against the same Postgres cluster.
- Every commit needs the AI-attribution trailer block from `CLAUDE.md` ("Commits — AI attribution trailers"), via `git commit -F <message-file>`.

---

### Task 1: Shared sort-key helpers + Collection/Wantlist row sort

**Files:**
- Modify: `backend/db.py:1367-1379` (insert helpers before `_canonical_artist_list`; leave `_canonical_artist_list` itself for Task 3)
- Modify: `backend/db.py:829` (`get_library_releases`'s artist `sort_expr`)
- Test: `backend/tests/test_catalog_crud.py`

**Interfaces:**
- Produces: `_artist_sort_sql(column: str) -> str` — SQL fragment usable directly in an `ORDER BY`. `_artist_sort_key(name: str) -> str` — Python sort key. Both live at module scope in `db.py`, above `_canonical_artist_list`, so Tasks 2 and 3 can call them.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_catalog_crud.py` (the module already defines `_catalog(admin_conn, discogs_id, artist, title)` — reuse it, don't redefine):

```python
def test_get_library_releases_sorts_the_prefixed_artists_by_the_following_word(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    _catalog(admin_conn, "r1", "The Beatles", "Abbey Road")
    _catalog(admin_conn, "r2", "Aphex Twin", "Selected Ambient Works")
    _catalog(admin_conn, "r3", "Pavement", "Slanted and Enchanted")
    _catalog(admin_conn, "r4", "Zappa", "Hot Rats")
    for rid in ("r1", "r2", "r3", "r4"):
        db.upsert_library_item(admin_conn, alice["id"], rid, in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        asc = db.get_library_releases(conn, alice["id"], sort="artist", order="asc")
        desc = db.get_library_releases(conn, alice["id"], sort="artist", order="desc")
    # "The Beatles" sorts under B, ahead of "Pavement" -- the full-string
    # key "the beatles" would instead sort it *after* "pavement", so this
    # ordering only holds with article-stripping applied, not by accident.
    assert [r["discogs_id"] for r in asc["releases"]] == ["r2", "r1", "r3", "r4"]
    assert [r["discogs_id"] for r in desc["releases"]] == ["r4", "r3", "r1", "r2"]
    # Display text is untouched by the sort-key transform.
    assert asc["releases"][1]["artist"] == "The Beatles"


def test_get_library_releases_the_prefix_sort_leaves_false_positives_alone(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    _catalog(admin_conn, "r1", "The", "Untitled")
    _catalog(admin_conn, "r2", "Theatre of Hate", "Westworld")
    _catalog(admin_conn, "r3", "The Who", "Tommy")
    for rid in ("r1", "r2", "r3"):
        db.upsert_library_item(admin_conn, alice["id"], rid, in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"], sort="artist", order="asc")
    # "The Who" -> sort key "who" (W). "The" and "Theatre of Hate" have no
    # word after "the " to strip, so they keep their literal spelling (T).
    assert [r["discogs_id"] for r in result["releases"]] == ["r1", "r2", "r3"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_catalog_crud.py -k the_prefixed_artists -v`
Expected: FAIL — both new tests fail their `assert` (current plain-alphabetical sort puts "The Beatles" and "The Who" under T).

- [ ] **Step 3: Add the helpers and wire them into `get_library_releases`**

In `backend/db.py`, immediately before `def _canonical_artist_list(conn, artists) -> list:` (currently line 1369), insert:

```python
_ARTIST_SORT_ARTICLE = "the "


def _artist_sort_sql(column: str) -> str:
    """SQL sort-key expression for `column`: a leading "The " (any case) is
    dropped so "The Beatles" sorts under B, matching the library-catalog
    convention "Beatles, The". Display text is untouched -- this is ORDER BY
    only. `LIKE 'the %'` requires a following word, so a bare "The" or a name
    like "Theatre of Hate" is correctly left alone."""
    return (
        f"CASE WHEN LOWER({column}) LIKE 'the %' "
        f"THEN LOWER(SUBSTRING({column} FROM {len(_ARTIST_SORT_ARTICLE) + 1})) "
        f"ELSE LOWER({column}) END"
    )


def _artist_sort_key(name: str) -> str:
    """Python equivalent of _artist_sort_sql, for the sidebar list which sorts
    in Python (see _canonical_artist_list) rather than SQL."""
    lower = name.lower()
    return lower[len(_ARTIST_SORT_ARTICLE):] if lower.startswith(_ARTIST_SORT_ARTICLE) else lower
```

Then in `get_library_releases`, change (currently line 829):

```python
        sort_expr = "LOWER(c.artist)" if sort_col == "artist" else f"c.{sort_col}"
```

to:

```python
        sort_expr = _artist_sort_sql("c.artist") if sort_col == "artist" else f"c.{sort_col}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_catalog_crud.py -v`
Expected: PASS — the whole file, not just the new tests, to catch any regression in existing artist-sort assertions.

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_catalog_crud.py
cat > /tmp/task1-commit.txt << 'EOF'
feat: sort "The"-prefixed bands by their next word

Collection/Wantlist row list only in this commit; Store/Track and the
artist sidebar follow in later commits on this branch.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
git commit -F /tmp/task1-commit.txt
```

---

### Task 2: Store/Track row sort

**Files:**
- Modify: `backend/db.py:1506` (`get_stock_items`'s artist `sort_expr`)
- Test: `backend/tests/test_stock_crud.py`

**Interfaces:**
- Consumes: `_artist_sort_sql(column: str) -> str` from Task 1.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_stock_crud.py` (the module already defines `_register(admin_conn, site_name) -> int` — reuse it):

```python
def test_get_stock_items_sorts_the_prefixed_artists_by_the_following_word(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    crawler_id = _register(admin_conn, "Amazon")
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "The Beatles", "title": "Abbey Road", "url": "https://x/1", "price": 20.0, "currency": "USD"},
        {"artist": "Aphex Twin", "title": "Selected Ambient Works", "url": "https://x/2", "price": 15.0, "currency": "USD"},
        {"artist": "Pavement", "title": "Slanted and Enchanted", "url": "https://x/3", "price": 12.0, "currency": "USD"},
        {"artist": "Zappa", "title": "Hot Rats", "url": "https://x/4", "price": 18.0, "currency": "USD"},
    ])
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], sort="artist", order="asc")
    # "The Beatles" sorts ahead of "Pavement" only with article-stripping --
    # the full-string key "the beatles" would put it after "pavement".
    assert [i["title"] for i in result["items"]] == [
        "Selected Ambient Works", "Abbey Road", "Slanted and Enchanted", "Hot Rats",
    ]
    assert result["items"][1]["artist"] == "The Beatles"


def test_get_stock_items_the_prefix_sort_leaves_false_positives_alone(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    crawler_id = _register(admin_conn, "Amazon")
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "The", "title": "Untitled", "url": "https://x/1", "price": 5.0, "currency": "USD"},
        {"artist": "Theatre of Hate", "title": "Westworld", "url": "https://x/2", "price": 6.0, "currency": "USD"},
        {"artist": "The Who", "title": "Tommy", "url": "https://x/3", "price": 7.0, "currency": "USD"},
    ])
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], sort="artist", order="asc")
    assert [i["title"] for i in result["items"]] == ["Untitled", "Westworld", "Tommy"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_stock_crud.py -k the_prefixed_artists -v`
Expected: FAIL — both new tests fail their `assert`.

- [ ] **Step 3: Wire `_artist_sort_sql` into `get_stock_items`**

In `backend/db.py`, change (currently line 1506):

```python
        sort_expr = "LOWER(s.artist)" if sort_col == "artist" else f"s.{sort_col}"
```

to:

```python
        sort_expr = _artist_sort_sql("s.artist") if sort_col == "artist" else f"s.{sort_col}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_stock_crud.py -v`
Expected: PASS — the whole file.

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_stock_crud.py
cat > /tmp/task2-commit.txt << 'EOF'
feat: sort "The"-prefixed bands by their next word in Store/Track

Reuses _artist_sort_sql from the Collection/Wantlist commit.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
git commit -F /tmp/task2-commit.txt
```

---

### Task 3: Artist-filter sidebar (all four tabs)

**Files:**
- Modify: `backend/db.py:1378` (`_canonical_artist_list`'s `sorted(...)` key)
- Test: `backend/tests/test_catalog_crud.py`, `backend/tests/test_stock_crud.py`

**Interfaces:**
- Consumes: `_artist_sort_key(name: str) -> str` from Task 1.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_catalog_crud.py`:

```python
def test_get_distinct_artists_sorts_the_prefixed_artists_by_the_following_word(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    _catalog(admin_conn, "r1", "The Beatles", "Abbey Road")
    _catalog(admin_conn, "r2", "Aphex Twin", "Selected Ambient Works")
    _catalog(admin_conn, "r3", "Pavement", "Slanted and Enchanted")
    _catalog(admin_conn, "r4", "Zappa", "Hot Rats")
    for rid in ("r1", "r2", "r3", "r4"):
        db.upsert_library_item(admin_conn, alice["id"], rid, in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        artists = db.get_distinct_artists(conn, alice["id"])
    # "The Beatles" sorts ahead of "Pavement" only with article-stripping --
    # the full-string key "the beatles" would put it after "pavement".
    assert artists == ["Aphex Twin", "The Beatles", "Pavement", "Zappa"]
```

Add to `backend/tests/test_stock_crud.py`:

```python
def test_get_distinct_stock_artists_sorts_the_prefixed_artists_by_the_following_word(admin_conn):
    crawler_id = _register(admin_conn, "Amazon")
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "The Beatles", "title": "Abbey Road", "url": "https://x/1", "price": 20.0, "currency": "USD"},
        {"artist": "Aphex Twin", "title": "Selected Ambient Works", "url": "https://x/2", "price": 15.0, "currency": "USD"},
        {"artist": "Pavement", "title": "Slanted and Enchanted", "url": "https://x/3", "price": 12.0, "currency": "USD"},
        {"artist": "Zappa", "title": "Hot Rats", "url": "https://x/4", "price": 18.0, "currency": "USD"},
    ])
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        artists = db.get_distinct_stock_artists(conn, alice["id"])
    assert artists == ["Aphex Twin", "The Beatles", "Pavement", "Zappa"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_catalog_crud.py::test_get_distinct_artists_sorts_the_prefixed_artists_by_the_following_word tests/test_stock_crud.py::test_get_distinct_stock_artists_sorts_the_prefixed_artists_by_the_following_word -v`
Expected: FAIL — both fail their `assert` (current order is `["Aphex Twin", "Pavement", "The Beatles", "Zappa"]`, plain-alphabetical on the full string).

- [ ] **Step 3: Wire `_artist_sort_key` into `_canonical_artist_list`**

In `backend/db.py`, change (currently line 1378):

```python
    return sorted(deduped, key=lambda a: (a.lower(), a))
```

to:

```python
    return sorted(deduped, key=lambda a: (_artist_sort_key(a), a.lower(), a))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_catalog_crud.py tests/test_stock_crud.py -v`
Expected: PASS — both full files, since `_canonical_artist_list` backs sidebar tests across both.

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest -v`
Expected: PASS — full suite green, confirming no other test asserted the old plain-alphabetical artist order.

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_catalog_crud.py backend/tests/test_stock_crud.py
cat > /tmp/task3-commit.txt << 'EOF'
feat: sort "The"-prefixed bands by their next word in the artist sidebar

Completes the "The"-prefix sort across all four tabs -- row list
(prior two commits) and artist-filter sidebar (this one) now agree.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
git commit -F /tmp/task3-commit.txt
```
