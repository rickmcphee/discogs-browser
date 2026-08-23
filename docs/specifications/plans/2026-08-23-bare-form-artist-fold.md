# Bare-Form Artist Fold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fold a bare artist spelling ("Beatles") into the same sidebar group and filter as its "The X"/"X, The" variants ("The Beatles" / "Beatles, The"), displaying the group as "Beatles, The".

**Architecture:** `db.canonical_artist_labels` gains a new lookup phase, run before its existing per-table casing loop, that checks `catalog` then `stock_items` for a "The"/comma-form spelling of a bare input before falling back to today's casing-only resolution. `get_library_releases`/`get_stock_items`'s `artist=` equality filter swaps its `_the_comma_form_sql` fold for `_artist_sort_sql`'s bare-core fold, which already reduces all three spellings to one key, so a filter click matches all three raw forms too.

**Tech Stack:** Python 3.9, FastAPI, psycopg (Postgres), pytest/pytest-asyncio.

**Spec:** [`docs/specifications/shaping/2026-08-22-bare-form-artist-fold-design.md`](../shaping/2026-08-22-bare-form-artist-fold-design.md)

## Global Constraints

- Python ≥3.9 — no `str | None` syntax; use `Optional[str]` or leave untyped.
- No comments unless the WHY is non-obvious — this codebase's existing `db.py` comments are dense with rationale; match that density for new code, don't add comments that restate what the code does.
- No backwards-compat shims — just change the code.
- Every commit needs the AI-attribution trailer block (see root `CLAUDE.md` "Commits — AI attribution trailers"). Create commits via `git commit -F <message-file>`, not `git commit -m`.
- Tests: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest`. Run pytest in the foreground, one invocation at a time — two concurrent runs against the same Postgres cluster both die (exit 137).
- Every new SQL fragment that folds artist casing must use Postgres's own `LOWER()`, not Python's `str.lower()` — see `canonical_artist_labels`' existing docstring rationale (İsis/U+0130). Don't introduce a Python-side fold.

---

### Task 1: `_artist_sort_sql` gains `escape_percent`; add bare-form expression indexes

**Files:**
- Modify: `backend/db.py:1521-1549` (`_artist_sort_sql`)
- Modify: `backend/db.py:285-301` (`GLOBAL_SCHEMA` f-string continuation)
- Test: `backend/tests/test_global_schema.py`

**Interfaces:**
- Consumes: `_ARTIST_SORT_ARTICLE`, `_ARTIST_SORT_SUFFIX` (module-level constants, `backend/db.py:65-66`, unchanged).
- Produces: `_artist_sort_sql(column: str, *, escape_percent: bool = True) -> str` — same CASE-expression output as today for `escape_percent=True` (the default, so both existing call sites at `db.py:937` and `db.py:1705` are unaffected); `escape_percent=False` emits the same expression with a single unescaped `%` in each `LIKE` pattern, for use in unparameterized DDL. New indexes `catalog_artist_bare_lower_idx`, `stock_items_artist_bare_lower_idx`, consumed by Task 3 and Task 4.

- [ ] **Step 1: Write the failing test for the new indexes**

Add to `backend/tests/test_global_schema.py`, right after `test_the_comma_form_indexes_have_unescaped_like_pattern`:

```python
def test_bare_form_indexes_have_unescaped_like_pattern(admin_conn):
    # Same rationale as test_the_comma_form_indexes_have_unescaped_like_pattern,
    # for _artist_sort_sql's two LIKE guards (leading "the " and trailing
    # ", the") instead of _the_comma_form_sql's one -- see
    # docs/specifications/shaping/2026-08-22-bare-form-artist-fold-design.md.
    for idx in ("catalog_artist_bare_lower_idx", "stock_items_artist_bare_lower_idx"):
        indexdef = admin_conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = %s", [idx]
        ).fetchone()["indexdef"]
        assert "the %%" not in indexdef.lower()
        assert "the %" in indexdef.lower()
        assert ", the%%" not in indexdef.lower()
        assert "%, the" in indexdef.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_global_schema.py::test_bare_form_indexes_have_unescaped_like_pattern -v`

Expected: FAIL — `fetchone()` returns `None` (index doesn't exist yet), `NoneType` has no `.fetchone()["indexdef"]` (`TypeError`).

- [ ] **Step 3: Add `escape_percent` to `_artist_sort_sql`**

Replace the whole function at `backend/db.py:1521-1549`:

```python
def _artist_sort_sql(column: str, *, escape_percent: bool = True) -> str:
    """SQL sort-key expression for `column`: a leading "The " or a trailing
    ", The" (either case) is dropped to the bare remainder, so "The Beatles"
    sorts under B like "Beatles, The" does, and -- now that both raw storage
    conventions coexist -- the two spellings of the same artist produce the
    *identical* key instead of merely landing in the same neighborhood. Only
    one guard can ever fire per name (canonical_artist_labels' own fold means
    a value is never both "The X" and "X, The" at once), so this stays a
    two-branch CASE, not a composition. Stripping to the bare word rather than
    to the comma-form ("beatles" rather than "beatles, the") matters: the
    comma-form key would still be wrong relative to a third artist like
    "Beatles A" ("beatles a" sorts before "beatles, the" but the bare key
    "beatles" -- correctly -- doesn't). This is ORDER BY only, on the raw
    un-canonicalized column -- it runs before canonical_artist_labels' fold
    gets a chance to relabel the row, so it needs its own stripping regardless
    of what the row's eventual display label becomes. `LIKE 'the %%'`/
    `LIKE '%%, the'` each require a real word on the far side, so a bare "The"
    or a name like "Theatre of Hate" is correctly left alone.

    This same bare, article-stripped key is also what the artist equality
    filters in get_library_releases/get_stock_items compare on (see
    docs/specifications/shaping/2026-08-22-bare-form-artist-fold-design.md):
    "The Beatles", "Beatles, The", and bare "Beatles" all reduce to the
    identical key "beatles", so a filter click matches all three raw
    spellings, not just the two _the_comma_form_sql folds together.

    escape_percent mirrors _the_comma_form_sql's parameter and exists for the
    same reason: doubled `%%` is required by every call site that executes
    with a non-empty params dict (both ORDER BY call sites, the two artist
    filters), but the unparameterized GLOBAL_SCHEMA index DDL needs the
    single, unescaped `%` Postgres actually sees, or the index bakes in a
    different string literal than the query compares against and is never
    chosen by the planner."""
    percent = "%%" if escape_percent else "%"
    suffix_len = len(_ARTIST_SORT_SUFFIX)
    return (
        f"CASE "
        f"WHEN LOWER({column}) LIKE '{_ARTIST_SORT_ARTICLE}{percent}' "
        f"THEN LOWER(SUBSTRING({column} FROM {len(_ARTIST_SORT_ARTICLE) + 1})) "
        f"WHEN LOWER({column}) LIKE '{percent}{_ARTIST_SORT_SUFFIX}' "
        f"THEN LOWER(SUBSTRING({column} FROM 1 FOR LENGTH({column}) - {suffix_len})) "
        f"ELSE LOWER({column}) END"
    )
```

- [ ] **Step 4: Add the new indexes to `GLOBAL_SCHEMA`**

`backend/db.py:285-301` currently reads:

```python
CREATE INDEX IF NOT EXISTS catalog_artist_lower_idx ON catalog (LOWER(artist));
CREATE INDEX IF NOT EXISTS stock_items_artist_lower_idx ON stock_items (LOWER(artist));
""" + f"""
-- Same reasoning as the two indexes above, for the "The X" -> "X, The"
-- comma-suffix fold layered on top (see canonical_artist_labels and the
-- artist filters in get_library_releases/get_stock_items). The plain
-- LOWER(artist) indexes above still serve _library_match_fragment's
-- owned-artist join, which doesn't use this fold -- see
-- docs/specifications/shaping/2026-08-16-the-suffix-artist-display-design.md.
-- escape_percent=False: this DDL runs with no params (see init_global_schema),
-- so the expression must match, character for character, what the query
-- sites see after psycopg's own substitution -- see _the_comma_form_sql.
CREATE INDEX IF NOT EXISTS catalog_artist_the_lower_idx
    ON catalog (LOWER({_the_comma_form_sql("artist", escape_percent=False)}));
CREATE INDEX IF NOT EXISTS stock_items_artist_the_lower_idx
    ON stock_items (LOWER({_the_comma_form_sql("artist", escape_percent=False)}));
"""
```

Change the final `"""` to append a third concatenated block:

```python
CREATE INDEX IF NOT EXISTS catalog_artist_lower_idx ON catalog (LOWER(artist));
CREATE INDEX IF NOT EXISTS stock_items_artist_lower_idx ON stock_items (LOWER(artist));
""" + f"""
-- Same reasoning as the two indexes above, for the "The X" -> "X, The"
-- comma-suffix fold layered on top (see canonical_artist_labels and the
-- artist filters in get_library_releases/get_stock_items). The plain
-- LOWER(artist) indexes above still serve _library_match_fragment's
-- owned-artist join, which doesn't use this fold -- see
-- docs/specifications/shaping/2026-08-16-the-suffix-artist-display-design.md.
-- escape_percent=False: this DDL runs with no params (see init_global_schema),
-- so the expression must match, character for character, what the query
-- sites see after psycopg's own substitution -- see _the_comma_form_sql.
CREATE INDEX IF NOT EXISTS catalog_artist_the_lower_idx
    ON catalog (LOWER({_the_comma_form_sql("artist", escape_percent=False)}));
CREATE INDEX IF NOT EXISTS stock_items_artist_the_lower_idx
    ON stock_items (LOWER({_the_comma_form_sql("artist", escape_percent=False)}));
""" + f"""
-- Bare-form artist fold (2026-08-22-bare-form-artist-fold-design.md): the
-- artist filters in get_library_releases/get_stock_items now compare
-- _artist_sort_sql's bare, article-stripped key instead of
-- _the_comma_form_sql's, so a bare "Beatles" row matches a "Beatles, The"
-- filter value too. Without a matching index that comparison is a
-- sequential scan of catalog or stock_items on every artist-filtered
-- listing page. _artist_sort_sql already lowers every branch of its own
-- CASE expression, so -- unlike the _the_comma_form_sql indexes above --
-- this isn't wrapped in an extra LOWER(). escape_percent=False for the same
-- reason as the comma-form indexes: this DDL runs with no params.
CREATE INDEX IF NOT EXISTS catalog_artist_bare_lower_idx
    ON catalog ({_artist_sort_sql("artist", escape_percent=False)});
CREATE INDEX IF NOT EXISTS stock_items_artist_bare_lower_idx
    ON stock_items ({_artist_sort_sql("artist", escape_percent=False)});
"""
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_global_schema.py -v`

Expected: PASS, including the pre-existing `test_the_comma_form_indexes_have_unescaped_like_pattern` (unaffected).

- [ ] **Step 6: Run the full backend suite to confirm no regressions**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest`

Expected: PASS (existing `_artist_sort_sql` call sites at `db.py:937`/`db.py:1705` produce byte-identical SQL to before, since `escape_percent` defaults to `True`).

- [ ] **Step 7: Commit**

```bash
git add backend/db.py backend/tests/test_global_schema.py
git commit -F <message-file>
```

Message body: `Add escape_percent to _artist_sort_sql; index its bare-form key` plus the required AI-attribution trailer block.

---

### Task 2: Fold a bare artist spelling into its "The X"/"X, The" group

**Files:**
- Modify: `backend/db.py:1463-1518` (`_CANONICAL_ARTIST_SQL`, `canonical_artist_labels`)
- Test: `backend/tests/test_catalog_crud.py`
- Test: `backend/tests/test_stock_crud.py`

**Interfaces:**
- Consumes: `_the_comma_form_sql` (`backend/db.py:69`, unchanged), `_ARTIST_SORT_ARTICLE`/`_ARTIST_SORT_SUFFIX` (`backend/db.py:65-66`).
- Produces: `_is_bare_artist_input(name: str) -> bool` (new, module-level). `canonical_artist_labels(conn, artists) -> dict` keeps its existing signature and return shape (`{raw_input: display_label}`); a bare input with a "The"/comma-form variant anywhere in `catalog` or `stock_items` now maps to that variant's comma-suffix label instead of its own casing group.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_catalog_crud.py`, after `test_get_library_releases_search_matches_comma_form_against_the_prefixed_row`:

```python
def test_get_distinct_artists_folds_bare_form_into_the_suffix_group(admin_conn):
    # "Beatles" carries no article marker at all -- unlike "The Beatles" vs
    # "Beatles, The", there's no string transform that says these are the
    # same artist; canonical_artist_labels has to look up whether a
    # The/comma-form spelling exists elsewhere. See
    # docs/specifications/shaping/2026-08-22-bare-form-artist-fold-design.md.
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    _catalog(admin_conn, "r1", "The Beatles", "Abbey Road")
    _catalog(admin_conn, "r2", "Beatles", "Let It Be")
    for rid in ("r1", "r2"):
        db.upsert_library_item(admin_conn, alice["id"], rid, in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.get_distinct_artists(conn, alice["id"]) == ["Beatles, The"]


def test_get_distinct_artists_bare_form_with_no_the_variant_stays_bare(admin_conn):
    # A genuinely bare-named artist, with no "The X"/"X, The" spelling
    # anywhere, must be unaffected -- the fold only fires when a variant
    # actually exists to fold into.
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    _catalog(admin_conn, "r1", "Nirvana", "Nevermind")
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.get_distinct_artists(conn, alice["id"]) == ["Nirvana"]
```

Add to `backend/tests/test_stock_crud.py`, after `test_get_stock_items_search_matches_comma_form_against_the_prefixed_row`:

```python
def test_get_distinct_stock_artists_folds_bare_form_across_tables(admin_conn):
    # The bare "Beatles" row lives only in stock_items; the "The Beatles"
    # spelling lives only in catalog. canonical_artist_labels' bare lookup
    # must check both tables before falling back to the bare input's own
    # casing group -- a naive single-table-first resolution would stop at
    # stock_items (which already has a winner for bare "Beatles" itself) and
    # never check catalog for the variant. See
    # docs/specifications/shaping/2026-08-22-bare-form-artist-fold-design.md.
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "The Beatles", "title": "Abbey Road",
        "year": None, "label": None, "format": None, "barcode": None,
        "cover_image_url": None, "discogs_url": None,
    })
    crawler_id = _register(admin_conn, "Amazon")
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Beatles", "title": "Let It Be", "url": "https://x/1", "price": 20.0, "currency": "USD"},
    ])
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        artists = db.get_distinct_stock_artists(conn, alice["id"])
        result = db.get_stock_items(conn, alice["id"], artist="Beatles, The")
    assert artists == ["Beatles, The"]
    assert result["total"] == 1
    assert result["items"][0]["artist"] == "Beatles, The"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_catalog_crud.py::test_get_distinct_artists_folds_bare_form_into_the_suffix_group tests/test_stock_crud.py::test_get_distinct_stock_artists_folds_bare_form_across_tables -v`

Expected: FAIL. First test: `get_distinct_artists` returns `["Beatles", "Beatles, The"]` (two entries) instead of one. Second test: `artists` includes a stray `"Beatles"` entry and/or `result["total"] == 0` (the `artist="Beatles, The"` filter, unchanged in this task, still only matches The/comma-form rows).

(`test_get_distinct_artists_bare_form_with_no_the_variant_stays_bare` already passes against current code — it's a regression guard, not new behavior. Confirm it passes both before and after this task's implementation step.)

- [ ] **Step 3: Add the bare-form lookup phase**

This step only touches the region from the end of `_CANONICAL_ARTIST_SQL` (`backend/db.py:1479`, the closing `"""` of that constant) through the end of `canonical_artist_labels` (`backend/db.py:1518`, by original line numbers — Task 1 shifted everything below `_artist_sort_sql` down by a few lines, but this region is entirely above `_artist_sort_sql`, so it's unaffected). Do **not** touch `_artist_sort_sql` or `_artist_sort_key` in this step — Task 1 already edited `_artist_sort_sql` to add `escape_percent`, and that edit must survive untouched.

First, insert two new definitions immediately after `_CANONICAL_ARTIST_SQL`'s closing `"""` and before `def canonical_artist_labels(...)`:

```python
def _is_bare_artist_input(name: str) -> bool:
    """True when `name` carries neither the "The X" nor "X, The" marker, so
    canonical_artist_labels' pure string fold (_the_comma_form_sql) can't
    tell it apart from an artist genuinely named without an article -- the
    case its bare-form lookup phase exists to resolve via a data lookup
    instead. See
    docs/specifications/shaping/2026-08-22-bare-form-artist-fold-design.md."""
    lower = name.lower()
    return not lower.startswith(_ARTIST_SORT_ARTICLE) and not lower.endswith(_ARTIST_SORT_SUFFIX)


# Bare-form lookup: for an input with neither marker (_is_bare_artist_input),
# does a "The X"/"X, The" spelling of the same name exist anywhere in this
# table? Reuses _CANONICAL_ARTIST_SQL's grouped/winner shape exactly, but the
# `wanted`/`winner` join key is the input's *implied* The-form
# (LOWER(artist) || ', the') rather than the input's own folded key -- a bare
# "Beatles" input never has a row literally matching its own key end in
# ", the", so this can't reuse the plain join in _CANONICAL_ARTIST_SQL as-is.
_CANONICAL_ARTIST_BARE_SQL = """
    WITH wanted AS (
        SELECT DISTINCT a AS artist FROM unnest(%(artists)s::text[]) AS a
    ),
    grouped AS (
        SELECT LOWER({the_bare}) AS key, artist AS label, COUNT(*) AS n
        FROM {table}
        WHERE LOWER({the_bare}) = ANY (ARRAY(SELECT LOWER(artist) || ', the' FROM wanted))
        GROUP BY LOWER({the_bare}), artist
    ),
    winner AS (
        SELECT DISTINCT ON (key) key, label FROM grouped
        ORDER BY key, n DESC, label COLLATE "C"
    )
    SELECT w.artist AS input, {the_label} AS label
    FROM wanted w JOIN winner ON winner.key = LOWER(w.artist) || ', the'
"""
```

Second, replace the existing `canonical_artist_labels` function entirely (its signature, docstring, and body — everything from `def canonical_artist_labels(conn, artists) -> dict:` through the final `return labels`) with:

```python
def canonical_artist_labels(conn, artists) -> dict:
    """Map each artist name, exactly as stored, to the label the UI displays.

    Stores disagree with each other and with Discogs on how to capitalize
    prepositions ("Jets to Brazil" vs "Jets To Brazil"), and
    normalize_artist_casing deliberately can't help: it only touches inputs
    that are entirely one case, since title-casing a mixed-case name mangles
    it. So the drift is resolved at read time instead, and `catalog` wins when
    it has the artist at all -- Discogs metadata is curated by hand, which no
    small-word list can reproduce ("The Jesus and Mary Chain", "clipping.").

    A second, independent fold is layered on top of the casing rule: "The X"
    and "X, The" -- the two conventions stores and Discogs disagree on -- are
    folded to the same grouping key via `_the_comma_form_sql`, and the winning
    casing (picked exactly as above) is then formatted to comma-suffix form
    for display, regardless of which raw spelling won. See
    docs/specifications/shaping/2026-08-16-the-suffix-artist-display-design.md.

    A third, independent lookup runs before either of the above for inputs
    with neither marker (_is_bare_artist_input): a bare "Beatles" carries no
    string transform that says it's the same artist as "The Beatles", so this
    checks whether a "The X"/"X, The" spelling of the same name exists
    anywhere in `catalog` or `stock_items` (catalog checked first, same
    preference as the casing fold) and, if so, resolves straight to that
    variant's label -- skipping the casing-only resolution below entirely.
    A bare input with no such variant anywhere falls through unresolved into
    the casing-only loop, unaffected. See
    docs/specifications/shaping/2026-08-22-bare-form-artist-fold-design.md.

    Both tables are global and un-RLS'd, so the label is app-wide: two users
    can't see one artist spelled two ways.
    """
    inputs = sorted({a for a in artists if a})
    labels: dict = {}

    bare_inputs = [a for a in inputs if _is_bare_artist_input(a)]
    for table in ("catalog", "stock_items"):
        remaining = [a for a in bare_inputs if a not in labels]
        if not remaining:
            break
        sql_text = _CANONICAL_ARTIST_BARE_SQL.format(
            table=table,
            the_bare=_the_comma_form_sql("artist"),
            the_label=_the_comma_form_sql("winner.label"),
        )
        rows = conn.execute(sql_text, {"artists": remaining}).fetchall()
        for row in rows:
            labels[row["input"]] = row["label"]

    for table in ("catalog", "stock_items"):
        remaining = [a for a in inputs if a not in labels]
        if not remaining:
            break
        sql_text = _CANONICAL_ARTIST_SQL.format(
            table=table,
            the_bare=_the_comma_form_sql("artist"),
            the_w=_the_comma_form_sql("w.artist"),
            the_label=_the_comma_form_sql("winner.label"),
        )
        rows = conn.execute(sql_text, {"artists": remaining}).fetchall()
        for row in rows:
            labels[row["input"]] = row["label"]
    return labels
```

Do not touch anything below this function — `_artist_sort_sql` (already carrying Task 1's `escape_percent` parameter) and `_artist_sort_key` come next in the file and are unrelated to this step.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_catalog_crud.py -k bare tests/test_stock_crud.py -k bare -v`

Expected: PASS.

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest`

Expected: PASS, including every pre-existing The/comma-fold test (`test_get_distinct_artists_folds_the_prefix_to_comma_suffix`, `test_get_distinct_stock_artists_merges_the_prefix_and_comma_suffix_spellings`, etc.) — this task adds a phase, it doesn't touch `_CANONICAL_ARTIST_SQL`'s own query text.

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_catalog_crud.py backend/tests/test_stock_crud.py
git commit -F <message-file>
```

Message body: `Fold a bare artist spelling into its The/comma-form group` plus the required AI-attribution trailer block.

---

### Task 3: `get_library_releases`'s artist filter matches the bare form

**Files:**
- Modify: `backend/db.py:903-912`
- Test: `backend/tests/test_catalog_crud.py`

**Interfaces:**
- Consumes: `_artist_sort_sql` with the `escape_percent` signature from Task 1 (default `True`, unchanged call shape from existing `db.py:937`).
- Produces: no new interface — `get_library_releases(conn, user_id, ..., artist=...)` keeps its existing signature; a bare-stored catalog row now satisfies `artist="Beatles, The"` alongside the two forms it already matched.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_catalog_crud.py`, after `test_get_distinct_artists_bare_form_with_no_the_variant_stays_bare` (added in Task 2):

```python
def test_get_library_releases_artist_filter_matches_bare_form_row(admin_conn):
    # Same shape as test_get_library_releases_artist_filter_matches_comma_form_against_the_prefixed_row,
    # for the bare spelling: clicking "Beatles, The" in the sidebar must also
    # surface a catalog row stored with no article at all.
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    _catalog(admin_conn, "r1", "The Beatles", "Abbey Road")
    _catalog(admin_conn, "r2", "Beatles", "Let It Be")
    for rid in ("r1", "r2"):
        db.upsert_library_item(admin_conn, alice["id"], rid, in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"], artist="Beatles, The")
    assert result["total"] == 2
    assert {r["artist"] for r in result["releases"]} == {"Beatles, The"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_catalog_crud.py::test_get_library_releases_artist_filter_matches_bare_form_row -v`

Expected: FAIL — `result["total"] == 1` (only `r1` matches; the bare `r2` row's `_the_comma_form_sql` fold is unchanged, so it stays `"beatles"` against the filter's folded `"beatles, the"`).

- [ ] **Step 3: Swap the filter's fold function**

`backend/db.py:903-912` currently reads:

```python
    if artist:
        # Both sides go through the same case fold and "The X" -> "X, The"
        # comma-suffix fold canonical_artist_labels applies: the sidebar always
        # sends back a post-fold label, so a raw compare would hide every
        # release whose catalog row spells the artist differently, including
        # rows literally stored as "The X" once the label reads "X, The".
        conditions.append(
            f"LOWER({_the_comma_form_sql('c.artist')}) = LOWER({_the_comma_form_sql('%(artist)s')})"
        )
        params["artist"] = artist
```

Replace with:

```python
    if artist:
        # Both sides go through the same bare, article-stripped fold
        # canonical_artist_labels' bare-form lookup relies on: the sidebar
        # always sends back a post-fold label, so a raw compare -- or even
        # _the_comma_form_sql's narrower The/comma-only fold -- would hide a
        # release whose catalog row spells the artist a third way, with no
        # article at all. _artist_sort_sql already lowers internally, so
        # this isn't wrapped in an extra LOWER() the way the comma-form fold
        # needs. See
        # docs/specifications/shaping/2026-08-22-bare-form-artist-fold-design.md.
        conditions.append(
            f"{_artist_sort_sql('c.artist')} = {_artist_sort_sql('%(artist)s')}"
        )
        params["artist"] = artist
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_catalog_crud.py::test_get_library_releases_artist_filter_matches_bare_form_row tests/test_catalog_crud.py::test_get_library_releases_artist_filter_matches_comma_form_against_the_prefixed_row tests/test_catalog_crud.py::test_get_library_releases_artist_filter_spans_casing_variants -v`

Expected: PASS — the new bare-form test, and both pre-existing filter tests (comma-form fold and casing-variant fold both remain strict subsets of `_artist_sort_sql`'s fold).

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_catalog_crud.py
git commit -F <message-file>
```

Message body: `Match the bare artist spelling in get_library_releases' artist filter` plus the required AI-attribution trailer block.

---

### Task 4: `get_stock_items`'s artist filter matches the bare form

**Files:**
- Modify: `backend/db.py:1717-1723`
- Test: `backend/tests/test_stock_crud.py`

**Interfaces:**
- Consumes: `_artist_sort_sql` (Task 1's signature).
- Produces: no new interface — `get_stock_items(conn, user_id, ..., artist=...)` keeps its existing signature; a bare-stored stock row now satisfies `artist="Beatles, The"` alongside the two forms it already matched.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_stock_crud.py`, after `test_get_distinct_stock_artists_folds_bare_form_across_tables` (added in Task 2):

```python
def test_get_stock_items_artist_filter_matches_bare_form_row(admin_conn):
    # Same shape as the catalog version in test_catalog_crud.py: clicking
    # "Beatles, The" must also surface a stock row stored with no article.
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    crawler_id = _register(admin_conn, "Amazon")
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "The Beatles", "title": "Abbey Road", "url": "https://x/1", "price": 20.0, "currency": "USD"},
        {"artist": "Beatles", "title": "Let It Be", "url": "https://x/2", "price": 18.0, "currency": "USD"},
    ])
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], artist="Beatles, The")
    assert result["total"] == 2
    assert {i["artist"] for i in result["items"]} == {"Beatles, The"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_stock_crud.py::test_get_stock_items_artist_filter_matches_bare_form_row -v`

Expected: FAIL — `result["total"] == 1`.

- [ ] **Step 3: Swap the filter's fold function**

`backend/db.py:1717-1723` currently reads:

```python
    if artist:
        # See the matching clause in get_library_releases: the filter value is
        # a canonical label, not any one store's spelling.
        conditions.append(
            f"LOWER({_the_comma_form_sql('s.artist')}) = LOWER({_the_comma_form_sql('%(artist)s')})"
        )
        params["artist"] = artist
```

Replace with:

```python
    if artist:
        # See the matching clause in get_library_releases: the filter value is
        # a canonical label, not any one store's spelling -- including, now,
        # a spelling with no article at all.
        conditions.append(
            f"{_artist_sort_sql('s.artist')} = {_artist_sort_sql('%(artist)s')}"
        )
        params["artist"] = artist
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_stock_crud.py::test_get_stock_items_artist_filter_matches_bare_form_row tests/test_stock_crud.py::test_get_distinct_stock_artists_merges_the_prefix_and_comma_suffix_spellings tests/test_stock_crud.py::test_get_stock_items_artist_filter_spans_casing_variants -v`

Expected: PASS. (If `test_get_stock_items_artist_filter_spans_casing_variants` isn't the exact pre-existing casing-filter test name, run `pytest tests/test_stock_crud.py -k "artist_filter" -v` instead and confirm all pass.)

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_stock_crud.py
git commit -F <message-file>
```

Message body: `Match the bare artist spelling in get_stock_items' artist filter` plus the required AI-attribution trailer block.

---

## Post-implementation

- Re-check the spec-drift grep across `docs/superpowers/specs/` and `docs/specifications/shaping/` for `canonical_artist_labels`, `_artist_sort_sql`, `_the_comma_form_sql`, `artist_the_lower_idx`, `artist_bare_lower_idx` before opening the PR (root `CLAUDE.md` "Pre-PR spec-drift check") — this plan already tracks the one spec it's implementing, but a grep is still required per that rule.
- Manual smoke check (no frontend code changes in this plan, but the sidebar consumes these endpoints): with the dev servers running, seed a "The Beatles" catalog row and a bare "Beatles" stock row for the same user, confirm the sidebar shows one "Beatles, The" entry, and confirm clicking it filters both.
