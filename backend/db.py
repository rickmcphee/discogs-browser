import hashlib
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import config
from logging_config import get_logger

log = get_logger("db")

_admin_pool: Optional[ConnectionPool] = None
_identity_pool: Optional[ConnectionPool] = None
_app_pool: Optional[ConnectionPool] = None

# price_paid's "unspecified" needs to be distinct from None: None means the
# user's custom "Price" field is genuinely empty and the stored value must be
# cleared, so it can't double as "this caller never looked."
_UNSET = object()


def get_admin_pool() -> ConnectionPool:
    global _admin_pool
    if _admin_pool is None:
        _admin_pool = ConnectionPool(
            config.DATABASE_URL, min_size=1, max_size=5, kwargs={"row_factory": dict_row}
        )
    return _admin_pool


def get_identity_pool() -> ConnectionPool:
    global _identity_pool
    if _identity_pool is None:
        _identity_pool = ConnectionPool(
            config.IDENTITY_DATABASE_URL, min_size=1, max_size=5, kwargs={"row_factory": dict_row}
        )
    return _identity_pool


def get_app_pool() -> ConnectionPool:
    global _app_pool
    if _app_pool is None:
        _app_pool = ConnectionPool(
            config.APP_DATABASE_URL, min_size=2, max_size=10, kwargs={"row_factory": dict_row}
        )
    return _app_pool


@contextmanager
def user_scope(user_id: int):
    """A connection from the RLS-scoped app_user role, with app.user_id set
    for the duration of one transaction. Every query run through this
    connection against library_items sees only that user's rows."""
    with get_app_pool().connection() as conn:
        conn.execute("SELECT set_config('app.user_id', %s, true)", [str(user_id)])
        yield conn


# Shared with _artist_sort_sql/_artist_sort_key and canonical_artist_labels'
# comma-suffix fold below, so the sort guard, the schema's expression indexes,
# and the label fold all agree on exactly which prefix/suffix counts as "The".
_ARTIST_SORT_ARTICLE = "the "
_ARTIST_SORT_SUFFIX = ", the"


def _the_comma_form_sql(column: str, *, escape_percent: bool = True) -> str:
    """SQL fragment: fold a leading "The " (any case) to a trailing ", The"
    -- "The Beatles" becomes "Beatles, The", the library-catalog convention
    some stores already use directly. A column already in that form, or with
    no leading article, passes through unchanged. Shares its `LIKE 'the %'`
    guard with `_artist_sort_sql` so a bare "The" or "Theatre of Hate" is left
    alone for the same reason. Used both to fold matching in SQL (schema
    indexes, canonical_artist_labels, the two artist search branches) and,
    inside canonical_artist_labels, to format the winning label for display.
    Not the artist-equality filters: those compare _artist_sort_sql's
    article-stripped key, which also matches a bare-spelled row (see
    docs/specifications/shaping/2026-08-22-bare-form-artist-fold-design.md).

    escape_percent doubles the LIKE pattern's `%` to `%%`, which psycopg's
    pyformat layer collapses back to a literal `%` -- required by every call
    site that executes with a non-empty params dict (canonical_artist_labels,
    both search branches), the default here. GLOBAL_SCHEMA has no params, so
    its two index definitions must pass escape_percent=False to keep the
    single, unescaped `%` Postgres actually sees: otherwise the index bakes in
    the literal two-character constant `'the %%'` while the query, after
    substitution, compares against `'the %'` -- a different string literal to
    the planner's expression-index matching, which requires an exact AST
    match, not merely equivalent LIKE semantics. An index built with the wrong
    constant is never used, silently."""
    percent = "%%" if escape_percent else "%"
    return (
        f"CASE WHEN LOWER({column}) LIKE '{_ARTIST_SORT_ARTICLE}{percent}' "
        f"THEN SUBSTRING({column} FROM {len(_ARTIST_SORT_ARTICLE) + 1}) || ', The' "
        f"ELSE {column} END"
    )


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
    "beatles" -- correctly -- doesn't). The ORDER BY call sites apply this to
    the raw un-canonicalized column -- it runs before canonical_artist_labels'
    fold gets a chance to relabel the row, so it needs its own stripping
    regardless of what the row's eventual display label becomes. `LIKE 'the %%'`/
    `LIKE '%%, the'` guard on the literal `'the '`/`', the'` substring, so a
    bare "The" or a name like "Theatre of Hate" is correctly left alone --
    though `%` also matches zero characters, so an artist spelled exactly
    ", The" (suffix branch) or "The " with a trailing space (prefix branch)
    keys to the empty string instead of being left alone; vanishingly
    unlikely names the guard wasn't written for, not a miss on the ones it
    was.

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
        f"(CASE "
        f"WHEN LOWER({column}) LIKE '{_ARTIST_SORT_ARTICLE}{percent}' "
        f"THEN LOWER(SUBSTRING({column} FROM {len(_ARTIST_SORT_ARTICLE) + 1})) "
        f"WHEN LOWER({column}) LIKE '{percent}{_ARTIST_SORT_SUFFIX}' "
        f"THEN LOWER(SUBSTRING({column} FROM 1 FOR (LENGTH({column}) - {suffix_len}))) "
        f"ELSE LOWER({column}) END)"
    )


def _price_sort_sql(column: str) -> str:
    """Numeric sort-key expression for a free-text price column.

    `library_items.price_paid` is whatever the user typed into their Discogs
    custom field, so it arrives as display text with the currency attached
    ("$30.00", "GBP 9", "1,200.00", "25,50"). Ordering that column as text is
    lexicographic -- "$100" sorts before "$9" -- so both the Collection/Wishlist
    Price header and the Track tab's price sort pull the leading number out and
    order on that instead. Whatever leads the value is skipped, not parsed, so
    the currency never reaches the comparison.

    The hard part is not the currency but the separators, because `,` and `.`
    swap roles between locales and the column holds both conventions. The
    grouping/decimal question is settled by matching the token against the
    formats that actually occur, rather than by stripping one character
    unconditionally:

    - `1,200` / `1,200.50` / `1,234,567` -- comma grouping, optional dot
      decimal. Commas drop out.
    - `1.234.567` / `1.234,56` -- dot grouping, optional comma decimal. Dots
      drop out and the comma becomes the decimal point. Two or more dot groups,
      or a comma decimal part, are required: a lone `1.234` stays a dot decimal,
      since reading it as 1234 would silently change how every existing
      three-decimal value already sorted.
    - `25,50` -- decimal comma, the case a blanket strip turns into 2550.
    - `1,23,456` / `1,23,456.78` -- the Indian grouping, whose last group is
      three digits and whose earlier ones are two, with the same optional dot
      decimal the comma-grouping branch takes. Commas drop out. The decimal
      suffix is not optional to *this* branch's usefulness: without it the
      commonest real form of the convention, a price with cents, missed every
      branch and floored to 1 -- the exact flattening recognising the grouping
      was meant to prevent.
    - `25` / `25.50` -- already plain.
    - `.99` / `,99` -- a bare decimal part, which a price written without its
      leading zero produces. The token may start with a separator and is given
      the zero back before the branches see it.
    - anything else falls back to the *leading digit run*, everything from the
      first separator on discarded.

    That fallback errs downward on purpose. Removing the separators instead --
    which is what it did until Copilot caught it on PR #172 -- reads the typo
    `"25.00.00"` as 250000, inflating a $25 record four orders of magnitude and
    floating it to the top of a descending sort. Truncating to `25` is wrong by
    the cents. An unrecognised token can only ever understate now, and only as
    far as its leading group; it can never outrank a well-formed larger price.
    That is the whole claim -- the fallback is a floor, not an estimate.

    The leading-zero rule is part of that claim rather than a nicety. While the
    token had to start with a digit, `"$.99"` captured as `99` and sorted a
    99-cent record above a $50 one -- the same inflation the fallback exists to
    rule out, arriving through the tokeniser instead of through the branches.
    Both ends have to hold for "can only understate" to mean anything.

    `1,200` is genuinely ambiguous -- 1200 grouped, or 1.2 with a decimal comma
    -- and is read as grouping, because three digits after a single comma is the
    grouping convention and a two-decimal price is what the decimal-comma form
    almost always looks like (`25,50`).

    Every branch yields digits with at most one dot, so the `::numeric` cast
    cannot fail and error the whole query -- the property that made a blanket
    strip-then-cast wrong in the first place. Anything with no digits at all
    ("N/A", "") yields NULL and sorts last via the caller's NULL guard.

    Mixed currencies are deliberately not converted: the number is compared
    as-is, which is right for the overwhelmingly common single-currency
    collection and merely approximate for the rare mixed one. The currency is
    untouched in the stored value, so display keeps it.
    """
    # Bound once in a subquery: the token is tested against several patterns and
    # repeating the regexp_match per branch would be unreadable and slower.
    return f"""(SELECT CASE
        WHEN t.v ~ '^[0-9]{{1,3}}(,[0-9]{{3}})+(\\.[0-9]+)?$' THEN replace(t.v, ',', '')
        WHEN t.v ~ '^[0-9]{{1,3}}(\\.[0-9]{{3}}){{2,}}$'
          OR t.v ~ '^[0-9]{{1,3}}(\\.[0-9]{{3}})+,[0-9]+$'
          THEN replace(replace(t.v, '.', ''), ',', '.')
        WHEN t.v ~ '^[0-9]{{1,2}}(,[0-9]{{2}})+,[0-9]{{3}}(\\.[0-9]+)?$' THEN replace(t.v, ',', '')
        WHEN t.v ~ '^[0-9]+,[0-9]+$' THEN replace(t.v, ',', '.')
        WHEN t.v ~ '^[0-9]+(\\.[0-9]+)?$' THEN t.v
        ELSE (regexp_match(t.v, '^[0-9]+'))[1]
    END::numeric
    FROM (SELECT regexp_replace(
            (regexp_match({column}, '[.,]?[0-9][0-9.,]*[0-9]|[.,]?[0-9]'))[1],
            '^([.,])', '0\\1') AS v) t)"""


GLOBAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog (
    discogs_id TEXT PRIMARY KEY,
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    year INTEGER,
    label TEXT,
    format TEXT,
    barcode TEXT,
    cover_image_url TEXT,
    discogs_url TEXT,
    last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS crawlers (
    id SERIAL PRIMARY KEY,
    site_name TEXT NOT NULL UNIQUE,
    module_path TEXT NOT NULL,
    crawler_type TEXT NOT NULL DEFAULT 'release',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    last_run TIMESTAMP
);

ALTER TABLE crawlers ADD COLUMN IF NOT EXISTS requires_discogs_release BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS app_config (
    id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS app_logs (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    level TEXT NOT NULL,
    logger_name TEXT NOT NULL,
    machine_id TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS app_logs_ts_idx ON app_logs (ts);

CREATE TABLE IF NOT EXISTS listings (
    id SERIAL PRIMARY KEY,
    release_id TEXT NOT NULL REFERENCES catalog(discogs_id),
    crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
    url TEXT NOT NULL,
    price DOUBLE PRECISION,
    shipping DOUBLE PRECISION,
    currency TEXT,
    condition TEXT,
    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(release_id, crawler_id)
);

CREATE TABLE IF NOT EXISTS stock_items (
    id SERIAL PRIMARY KEY,
    crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    format TEXT,
    price DOUBLE PRECISION,
    currency TEXT,
    url TEXT NOT NULL,
    cover_image_url TEXT,
    item_key TEXT,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS crawl_queue (
    id SERIAL PRIMARY KEY,
    discogs_id TEXT REFERENCES catalog(discogs_id),
    requested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'pending',
    claimed_by TEXT,
    claimed_at TIMESTAMP
);

-- Old crawl_queue_pending_idx dropped below, after item_key exists: this
-- table's composite replacement indexes on item_key, which isn't a column
-- here yet on a fresh install (added by the ALTER a few statements down).

CREATE TABLE IF NOT EXISTS stock_item_identities (
    item_key TEXT PRIMARY KEY,
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    format TEXT,
    last_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE crawl_queue ALTER COLUMN discogs_id DROP NOT NULL;
ALTER TABLE crawl_queue ADD COLUMN IF NOT EXISTS item_key TEXT REFERENCES stock_item_identities(item_key);

-- Serves claim_crawl_queue_batch's WHERE/ORDER BY directly: leading
-- (item_key IS NOT NULL) matches the release-before-stock sort, then
-- requested_at, id for FIFO within a kind. Partial so it stays small as rows
-- accumulate 'done' history. available_at is deliberately not a key column --
-- deferred rows are a small minority, so keeping the index ordered for the
-- sort beats indexing the filter. Named differently from the
-- crawl_queue_pending_idx it replaces because CREATE INDEX IF NOT EXISTS
-- under an unchanged name is a no-op against a database that already has the
-- old definition. Placed after item_key exists (not immediately after
-- CREATE TABLE crawl_queue above) since it indexes that column and a fresh
-- install has no item_key column until the ALTER just above runs.
DROP INDEX IF EXISTS crawl_queue_pending_idx;
CREATE INDEX IF NOT EXISTS crawl_queue_claimable_idx
    ON crawl_queue ((item_key IS NOT NULL), requested_at, id)
    WHERE status = 'pending';

-- pending_crawler_ids is the per-pass progress record: NULL means "every
-- crawler currently eligible for this target", a non-NULL array narrows the
-- next pass to unfinished work from an earlier one. listings cannot serve
-- this purpose -- an empty result writes no row at all, and clear_listing_price
-- leaves a NULL-price row behind, so neither absence nor presence of a row
-- distinguishes "not attempted" from "attempted, found nothing".
ALTER TABLE crawl_queue ADD COLUMN IF NOT EXISTS pending_crawler_ids INTEGER[];

-- When a pass finished a row, as distinct from when a pass claimed it.
-- claimed_at cannot stand in for it: a row fans out to one sequential search
-- per eligible crawler, so a row claimed well outside a reporting window can
-- still complete inside it. Measuring the drain rate off claimed_at therefore
-- reads zero -- and every ETA null -- while rows are actively completing.
ALTER TABLE crawl_queue ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP;

-- The drain-rate query asks for 'done' rows completed inside a short window,
-- on a timer, against a table holding a row for every target this app has ever
-- queued. Without this the poll scans the whole catalog's worth of history to
-- find a few minutes of it. Partial, so it indexes only the 'done' population
-- and stays out of the way of the pending path's own index.
CREATE INDEX IF NOT EXISTS crawl_queue_completed_idx
    ON crawl_queue (completed_at) WHERE status = 'done';

-- The other half of the same problem. queue_summary's totals aggregate reads
-- every row that is not 'done', and crawl_queue_claimable_idx cannot serve it
-- (partial on 'pending', so it sees neither in_progress rows nor the deferred
-- ones). Without this the poll seq-scans the whole table to find what is
-- usually a handful of live rows. Partial on the complement of 'done', so it
-- stays proportional to work in flight rather than to the catalog.
CREATE INDEX IF NOT EXISTS crawl_queue_active_idx
    ON crawl_queue (status) WHERE status <> 'done';

-- available_at is a not-before marker, set when a pass defers a crawler whose
-- site is in circuit-breaker cooldown. Without it the row would be re-claimed
-- on the very next batch and re-deferred in a hot loop.
ALTER TABLE crawl_queue ADD COLUMN IF NOT EXISTS available_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP;

-- One row per target, not per (target, crawler) pair: the crawler set is a
-- runtime decision resolved by _drain_one_batch against live crawlers state,
-- not something frozen into row data at enqueue time. Guarded on crawler_id
-- still existing so re-runs are no-ops.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'crawl_queue' AND column_name = 'crawler_id'
    ) THEN
        -- The surviving row per target is its lowest id. pending_crawler_ids
        -- inherits exactly that target's unfinished crawlers, so a pass that
        -- was in flight when the upgrade ran resumes with the right set
        -- instead of re-running crawlers that already finished.
        WITH collapsed AS (
            SELECT COALESCE(discogs_id, item_key) AS target,
                   MIN(id) AS keep_id,
                   array_agg(crawler_id ORDER BY crawler_id)
                       FILTER (WHERE status <> 'done') AS unfinished
            FROM crawl_queue
            GROUP BY COALESCE(discogs_id, item_key)
        )
        UPDATE crawl_queue cq
        SET status = CASE WHEN c.unfinished IS NULL THEN 'done' ELSE 'pending' END,
            pending_crawler_ids = c.unfinished,
            claimed_by = NULL,
            claimed_at = NULL
        FROM collapsed c
        WHERE cq.id = c.keep_id;

        DELETE FROM crawl_queue cq
        USING (
            SELECT COALESCE(discogs_id, item_key) AS target, MIN(id) AS keep_id
            FROM crawl_queue
            GROUP BY COALESCE(discogs_id, item_key)
        ) k
        WHERE COALESCE(cq.discogs_id, cq.item_key) = k.target AND cq.id <> k.keep_id;

        ALTER TABLE crawl_queue DROP CONSTRAINT IF EXISTS crawl_queue_discogs_id_crawler_id_key;
        DROP INDEX IF EXISTS crawl_queue_item_key_crawler_idx;
        ALTER TABLE crawl_queue DROP COLUMN crawler_id;
    END IF;
END $$;

-- Nullable-column unique indexes: exactly one of discogs_id/item_key is set
-- per row, multiple NULLs coexist, and ON CONFLICT (discogs_id) /
-- ON CONFLICT (item_key) infer them.
CREATE UNIQUE INDEX IF NOT EXISTS crawl_queue_discogs_id_idx ON crawl_queue (discogs_id);
CREATE UNIQUE INDEX IF NOT EXISTS crawl_queue_item_key_idx ON crawl_queue (item_key);

ALTER TABLE listings ALTER COLUMN release_id DROP NOT NULL;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS item_key TEXT REFERENCES stock_item_identities(item_key);
CREATE UNIQUE INDEX IF NOT EXISTS listings_item_key_crawler_idx ON listings (item_key, crawler_id);

-- Serves queue_summary's per-crawler activity lookups, which run on a timer:
-- for each enabled crawler, a count over a recent window (a range scan of just
-- that slice) and an ORDER BY last_checked DESC LIMIT 1 probe for recency (one
-- backward entry read). Both are correlated subqueries driven from crawlers,
-- specifically so neither has to visit listings outside the window.
CREATE INDEX IF NOT EXISTS listings_crawler_last_checked_idx ON listings (crawler_id, last_checked);

-- stock_items.item_key is not unique (the same artist/title/url can be seen
-- by several crawlers) so this is a plain index, not a unique one. Needed by
-- get_all_stock_judgments' LEFT JOIN LATERAL, and it also serves the
-- existing get_stock_items / get_unjudged_stock_items joins on s.item_key.
CREATE INDEX IF NOT EXISTS stock_items_item_key_idx ON stock_items (item_key);

-- One row per observed price drop on a record, written by whichever path just
-- wrote the price (see _record_price_drops). Global, with no user_id column,
-- for the same reason listings and stock_items are: it records that a *record*
-- got cheaper, which is a fact about the catalog. The crawl worker could not
-- fan it out per user even if the table wanted it to -- stock_item_saves is
-- RLS-scoped and its connections are unscoped app_user ones, so the worker
-- cannot see anybody's saves. The per-user half is a read-time join through
-- the caller's own saves under user_scope; see get_price_drop_notifications.
--
-- url/price/previous_best are denormalized rather than resolved at read time:
-- replace_stock_items deletes and reinserts a store's whole batch on every
-- sync, so a notification that resolved its link through the live table would
-- silently start pointing somewhere else -- or nowhere -- the moment the store
-- restocked. The drop is a fact about a listing that existed at that price at
-- that moment.
CREATE TABLE IF NOT EXISTS stock_item_price_drops (
    id BIGSERIAL PRIMARY KEY,
    item_key TEXT NOT NULL REFERENCES stock_item_identities(item_key),
    crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
    url TEXT NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    currency TEXT,
    previous_best DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Serves the notifications read path, which is always "this item_key's drops,
-- newest first" driven from the caller's saved keys, and the retention sweep's
-- created_at scan is a whole-table pass by design.
CREATE INDEX IF NOT EXISTS stock_item_price_drops_item_key_idx
    ON stock_item_price_drops (item_key, id DESC);

ALTER TABLE stock_items ADD COLUMN IF NOT EXISTS release_id TEXT REFERENCES catalog(discogs_id);
CREATE UNIQUE INDEX IF NOT EXISTS stock_items_crawler_release_idx ON stock_items (crawler_id, release_id) WHERE release_id IS NOT NULL;

-- Expression indexes, because every artist read path case-folds now: the
-- artist filters in get_library_releases/get_stock_items, and
-- canonical_artist_labels, which runs per page of either. Without these both
-- are sequential scans of the two largest tables on every browse request.
CREATE INDEX IF NOT EXISTS catalog_artist_lower_idx ON catalog (LOWER(artist));
CREATE INDEX IF NOT EXISTS stock_items_artist_lower_idx ON stock_items (LOWER(artist));
""" + f"""
-- Same reasoning as the two indexes above, for the "The X" -> "X, The"
-- comma-suffix fold layered on top. These serve canonical_artist_labels'
-- grouping WHERE; the artist filters moved to _artist_sort_sql's bare key
-- and are covered by the two bare-form indexes below. The plain
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


def init_global_schema():
    with get_admin_pool().connection() as conn:
        conn.execute(GLOBAL_SCHEMA)
        conn.commit()


TENANT_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    discogs_user_id INTEGER UNIQUE NOT NULL,
    discogs_username TEXT NOT NULL,
    discogs_oauth_token_encrypted BYTEA,
    discogs_oauth_secret_encrypted BYTEA,
    plex_base_url TEXT,
    plex_token TEXT,
    plex_match_threshold INTEGER NOT NULL DEFAULT 90,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    anthropic_api_key TEXT,
    recommendation_item_limit INTEGER NOT NULL DEFAULT 300,
    invited_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_image BYTEA;

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS library_items (
    user_id INTEGER NOT NULL REFERENCES users(id),
    discogs_id TEXT NOT NULL REFERENCES catalog(discogs_id),
    in_collection BOOLEAN NOT NULL DEFAULT FALSE,
    in_wishlist BOOLEAN NOT NULL DEFAULT FALSE,
    plex_url TEXT,
    plex_matched_at TIMESTAMP,
    last_synced TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, discogs_id)
);

ALTER TABLE library_items ADD COLUMN IF NOT EXISTS collection_date_added TIMESTAMP;
ALTER TABLE library_items ADD COLUMN IF NOT EXISTS wishlist_date_added TIMESTAMP;
ALTER TABLE library_items ADD COLUMN IF NOT EXISTS price_paid TEXT;

-- One-shot, self-retiring migration off the global catalog.discogs_price.
-- The guard is what makes it safe to leave in a schema string that re-runs on
-- every boot: once the source column is gone this whole block is a no-op, so
-- it can neither error nor resurrect a price a user deliberately cleared.
--
-- Only a release with exactly one collection owner is backfilled. The stored
-- global value is whichever user synced last, so with two owners it cannot be
-- attributed to either; copying it to both would be the same cross-tenant leak
-- in reverse. Contested rows stay NULL and self-heal on the owner's next
-- mode="all" sync.
--
-- Must live in TENANT_SCHEMA, not GLOBAL_SCHEMA: GLOBAL_SCHEMA runs first
-- (main.py), before library_items.price_paid exists.
DO $$
BEGIN
  -- Double-checked locking, and the order matters. The outer check is an
  -- unlocked catalog lookup, so once the column is gone -- which is the state
  -- on every boot after the one that migrates -- this block costs one cheap
  -- read and takes no lock at all. Taking the advisory lock unconditionally
  -- ahead of the check instead serializes every init_tenant_schema() call
  -- forever, for a migration that can never run again; that measurably slowed
  -- the test suite and starved its event-loop timing tests.
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name = 'catalog' AND column_name = 'discogs_price') THEN
    -- Serialize the migration itself across concurrently booting instances --
    -- fly.toml's min_machines_running is 2 (see the multi-machine design doc),
    -- so this is a real scenario, not a hypothetical one. Without this lock,
    -- two sessions could both pass the outer check and the loser would then
    -- UPDATE or DROP against a column
    -- the winner had already removed, failing the boot. The deployment spec
    -- claims multi-machine scaling needs no design change; this keeps that true.
    PERFORM pg_advisory_xact_lock(2026080901);

    -- Re-check under the lock: the winner may have finished while we waited.
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'catalog' AND column_name = 'discogs_price') THEN
      -- price_paid IS NULL is redundant on the real one-shot run, since the
      -- column is created empty in this same schema application. It matters if
      -- the source column is ever reintroduced -- a restore, or a rollback
      -- experiment -- where re-running must not clobber prices users have since
      -- recorded. A backfill fills blanks; it does not overwrite.
      UPDATE library_items li SET price_paid = c.discogs_price
      FROM catalog c
      WHERE c.discogs_id = li.discogs_id
        AND li.in_collection = TRUE
        AND c.discogs_price IS NOT NULL
        AND li.price_paid IS NULL
        AND (SELECT COUNT(*) FROM library_items x
             WHERE x.discogs_id = li.discogs_id AND x.in_collection = TRUE) = 1;

      ALTER TABLE catalog DROP COLUMN IF EXISTS discogs_price;
    END IF;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS stock_item_judgments (
    user_id INTEGER NOT NULL REFERENCES users(id),
    item_key TEXT NOT NULL,
    recommended BOOLEAN NOT NULL,
    reason TEXT,
    judged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, item_key)
);

CREATE TABLE IF NOT EXISTS user_hidden_crawlers (
    user_id INTEGER NOT NULL REFERENCES users(id),
    crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
    PRIMARY KEY (user_id, crawler_id)
);

CREATE TABLE IF NOT EXISTS stock_item_saves (
    user_id INTEGER NOT NULL REFERENCES users(id),
    item_key TEXT NOT NULL,
    saved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, item_key)
);

CREATE TABLE IF NOT EXISTS user_notification_reads (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    last_read_drop_id BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS invites (
    code TEXT PRIMARY KEY,
    created_by INTEGER REFERENCES users(id),
    redeemed_by INTEGER REFERENCES users(id),
    redeemed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE invites ADD COLUMN IF NOT EXISTS note TEXT;

-- Neither oauth_request_state nor pending_signups gets an RLS policy: both
-- are pre-session state with no per-user row-ownership column to scope a
-- policy on in the first place (unlike users/sessions, where the row IS
-- owned by a user and the omission is about grants doing the real work —
-- here a policy would have nothing meaningful to compare against).
CREATE TABLE IF NOT EXISTS oauth_request_state (
    request_token TEXT PRIMARY KEY,
    request_token_secret TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pending_signups (
    signup_token TEXT PRIMARY KEY,
    discogs_user_id INTEGER NOT NULL,
    discogs_username TEXT NOT NULL,
    oauth_token_encrypted BYTEA NOT NULL,
    oauth_secret_encrypted BYTEA NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE library_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE library_items FORCE ROW LEVEL SECURITY;
ALTER TABLE stock_item_judgments ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_item_judgments FORCE ROW LEVEL SECURITY;
ALTER TABLE user_hidden_crawlers ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_hidden_crawlers FORCE ROW LEVEL SECURITY;
ALTER TABLE stock_item_saves ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_item_saves FORCE ROW LEVEL SECURITY;
ALTER TABLE user_notification_reads ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_notification_reads FORCE ROW LEVEL SECURITY;

-- WITH CHECK is given explicitly (identical to USING) on every policy
-- below rather than left implicit. Postgres already defaults an omitted
-- WITH CHECK to the USING expression for a FOR-ALL policy like these --
-- verified directly against this project's Postgres 16 (an unscoped INSERT
-- is rejected with "new row violates row-level security policy" even
-- without this clause) -- but that default is easy to get wrong when
-- reasoning about the policy later (e.g. if it's ever split into separate
-- FOR SELECT/FOR INSERT policies, where the default no longer applies), so
-- it's spelled out for auditability rather than relied on implicitly.

-- Defense-in-depth only: the only role granted anything on users
-- (app_identity) has BYPASSRLS, so this policy has no operational effect
-- today. What actually protects users right now is that app_user has
-- no grant on this table at all.
DROP POLICY IF EXISTS users_isolation ON users;
CREATE POLICY users_isolation ON users
    USING (id = current_setting('app.user_id', true)::int)
    WITH CHECK (id = current_setting('app.user_id', true)::int);

-- Defense-in-depth only: the only role granted anything on sessions
-- (app_identity) has BYPASSRLS, so this policy has no operational effect
-- today. What actually protects sessions right now is that app_user has
-- no grant on this table at all.
DROP POLICY IF EXISTS sessions_isolation ON sessions;
CREATE POLICY sessions_isolation ON sessions
    USING (user_id = current_setting('app.user_id', true)::int)
    WITH CHECK (user_id = current_setting('app.user_id', true)::int);

DROP POLICY IF EXISTS library_items_isolation ON library_items;
CREATE POLICY library_items_isolation ON library_items
    USING (user_id = current_setting('app.user_id', true)::int)
    WITH CHECK (user_id = current_setting('app.user_id', true)::int);

DROP POLICY IF EXISTS stock_item_judgments_isolation ON stock_item_judgments;
CREATE POLICY stock_item_judgments_isolation ON stock_item_judgments
    USING (user_id = current_setting('app.user_id', true)::int)
    WITH CHECK (user_id = current_setting('app.user_id', true)::int);

DROP POLICY IF EXISTS user_hidden_crawlers_isolation ON user_hidden_crawlers;
CREATE POLICY user_hidden_crawlers_isolation ON user_hidden_crawlers
    USING (user_id = current_setting('app.user_id', true)::int)
    WITH CHECK (user_id = current_setting('app.user_id', true)::int);

DROP POLICY IF EXISTS stock_item_saves_isolation ON stock_item_saves;
CREATE POLICY stock_item_saves_isolation ON stock_item_saves
    USING (user_id = current_setting('app.user_id', true)::int)
    WITH CHECK (user_id = current_setting('app.user_id', true)::int);

DROP POLICY IF EXISTS user_notification_reads_isolation ON user_notification_reads;
CREATE POLICY user_notification_reads_isolation ON user_notification_reads
    USING (user_id = current_setting('app.user_id', true)::int)
    WITH CHECK (user_id = current_setting('app.user_id', true)::int);
"""


def _ensure_role(conn, role_name: str, password: str, bypass_rls: bool):
    exists = conn.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = %s", [role_name]
    ).fetchone()
    if not exists:
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role_name))
        )
    # Unconditional so re-running init_tenant_schema() after a password
    # rotation (IDENTITY_DB_PASSWORD/APP_DB_PASSWORD changed in the env)
    # actually updates the role in Postgres, not just its BYPASSRLS bit.
    conn.execute(
        sql.SQL("ALTER ROLE {} PASSWORD {} {}").format(
            sql.Identifier(role_name),
            sql.Literal(password),
            sql.SQL("BYPASSRLS" if bypass_rls else "NOBYPASSRLS"),
        )
    )


# Granting BYPASSRLS to a role requires the executing role to be a Postgres
# superuser or to already have BYPASSRLS itself (see CREATE/ALTER ROLE docs).
# The admin/DATABASE_URL role must satisfy that — true for the dev-default
# Docker `postgres` superuser, but not guaranteed for a managed-Postgres
# admin role in a real deployment, where this ALTER ROLE call would fail.
def init_tenant_schema():
    if not config.IDENTITY_DB_PASSWORD or not config.APP_DB_PASSWORD:
        raise RuntimeError(
            "IDENTITY_DB_PASSWORD and APP_DB_PASSWORD must both be set (non-empty) "
            "before creating app_identity/app_user roles"
        )
    with get_admin_pool().connection() as conn:
        conn.execute(TENANT_SCHEMA)
        _ensure_role(conn, "app_identity", config.IDENTITY_DB_PASSWORD, bypass_rls=True)
        _ensure_role(conn, "app_user", config.APP_DB_PASSWORD, bypass_rls=False)

        conn.execute("GRANT SELECT, INSERT, UPDATE ON users TO app_identity")
        conn.execute("GRANT SELECT, INSERT, UPDATE ON invites TO app_identity")
        # Older schema versions granted app_user INSERT on invites before invite
        # minting moved to app_identity. GRANT/REVOKE aren't self-reversing --
        # deleting that GRANT line from this function does not strip the
        # privilege from a role that already has it on an upgraded database, so
        # the old grant is revoked explicitly on every re-run. A no-op on a
        # database that never had it (or already had it revoked).
        conn.execute("REVOKE INSERT ON invites FROM app_user")
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON sessions TO app_identity")
        conn.execute("GRANT USAGE, SELECT ON SEQUENCE users_id_seq TO app_identity")
        conn.execute("GRANT SELECT, INSERT, DELETE ON oauth_request_state TO app_identity")
        conn.execute("GRANT SELECT, INSERT, DELETE ON pending_signups TO app_identity")

        # catalog/listings/stock_items/crawl_queue have no per-user owner column
        # to write an RLS policy against -- they're shared across every tenant
        # (the catalog is one global Discogs mirror, crawl_queue one shared work
        # list) -- so app_user's per-request connections need real INSERT/UPDATE
        # here despite there being no RLS to gate it; isolation for per-user data
        # instead comes entirely from library_items/stock_item_judgments' RLS
        # policies below and from library_items' own FK into catalog.
        # UPDATE (not just SELECT) is needed here too: update_crawler_last_run(),
        # also run through get_app_pool() from _sync_stock, updates crawlers.last_run
        # after each catalog crawl.
        conn.execute("GRANT SELECT, UPDATE ON crawlers TO app_user")
        # stock_items additionally needs DELETE (not just INSERT/UPDATE, unlike
        # catalog/listings): replace_stock_items(), run through get_app_pool()
        # from _sync_stock, deletes a crawler's whole prior batch before
        # reinserting the fresh one.
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON stock_items TO app_user")
        conn.execute("GRANT SELECT, INSERT, UPDATE ON catalog, listings, stock_item_identities TO app_user")
        # DELETE, but no UPDATE: a price drop is an append-only observation the
        # crawl worker records and the retention sweep (delete_expired_price_drops,
        # run from _sync_stock on this same role) eventually removes. Nothing
        # ever edits one in place.
        conn.execute("GRANT SELECT, INSERT, DELETE ON stock_item_price_drops TO app_user")
        conn.execute("GRANT USAGE, SELECT ON SEQUENCE listings_id_seq, stock_items_id_seq, stock_item_price_drops_id_seq TO app_user")
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON library_items TO app_user")
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON stock_item_judgments TO app_user")
        conn.execute("GRANT SELECT, INSERT, DELETE ON user_hidden_crawlers TO app_user")
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON stock_item_saves TO app_user")
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON user_notification_reads TO app_user")
        # crawl_queue needs DELETE for the same reason stock_items does:
        # delete_dead_stock_crawl_queue_rows(), run through get_app_pool() from
        # PATCH /api/crawlers/{id} and at the end of each stock sync.
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON crawl_queue TO app_user")
        conn.execute("GRANT USAGE, SELECT ON SEQUENCE crawl_queue_id_seq TO app_user")
        conn.commit()


def upsert_catalog_release(conn, data: dict):
    conn.execute(
        """
        INSERT INTO catalog (discogs_id, artist, title, year, label, format,
                              barcode, cover_image_url, discogs_url, last_synced)
        VALUES (%(discogs_id)s, %(artist)s, %(title)s, %(year)s, %(label)s, %(format)s,
                %(barcode)s, %(cover_image_url)s, %(discogs_url)s, CURRENT_TIMESTAMP)
        ON CONFLICT (discogs_id) DO UPDATE SET
            artist = EXCLUDED.artist, title = EXCLUDED.title, year = EXCLUDED.year,
            label = EXCLUDED.label, format = EXCLUDED.format,
            barcode = EXCLUDED.barcode, cover_image_url = EXCLUDED.cover_image_url,
            discogs_url = EXCLUDED.discogs_url, last_synced = CURRENT_TIMESTAMP
        """,
        data,
    )


def get_catalog_release(conn, discogs_id: str) -> Optional[dict]:
    return conn.execute(
        "SELECT * FROM catalog WHERE discogs_id = %s", [discogs_id]
    ).fetchone()


def get_stock_item_identity(conn, item_key: str) -> Optional[dict]:
    return conn.execute(
        "SELECT * FROM stock_item_identities WHERE item_key = %s", [item_key]
    ).fetchone()


# A price drop older than this is history rather than news: the Notifications
# tab never pages back that far, and the table takes a row for every drop the
# crawl worker sees -- including ones no user has saved, which it cannot tell
# apart (stock_item_saves is RLS-scoped and invisible to an unscoped app_user
# connection). Swept by delete_expired_price_drops at the end of each stock sync.
PRICE_DROP_RETENTION_DAYS = 90


def _normalized_currency(currency: Optional[str]) -> str:
    """The currency bucket a price competes in. Folds NULL to USD exactly as
    the frontend's formatPrice does -- most sources hardcode USD and the column
    postdates them -- so a legacy NULL row and a USD row are one bucket."""
    return (currency or "USD").upper()


def _price_floors(conn, item_keys: list) -> dict:
    """Lowest price currently recorded for each (item_key, currency), across
    both the stores that list a record and the marketplace listings crawled for
    it. Keys with no priced row anywhere are simply absent.

    Bucketed by currency, not pooled: this app carries no exchange rates, so
    EUR 10 and USD 12 are not comparable and must never be allowed to undercut
    one another. Non-USD sources are real here (Jetglow Recordings, SPV), so
    that is a live case rather than a hypothetical.
    """
    if not item_keys:
        return {}
    rows = conn.execute(
        """
        SELECT item_key, currency, MIN(price) AS floor
        FROM (
            SELECT s.item_key AS item_key,
                   COALESCE(UPPER(s.currency), 'USD') AS currency,
                   s.price AS price
            FROM stock_items s
            WHERE s.item_key = ANY(%(item_keys)s) AND s.price IS NOT NULL
            UNION ALL
            SELECT l.item_key,
                   COALESCE(UPPER(l.currency), 'USD'),
                   l.price
            FROM listings l
            WHERE l.item_key = ANY(%(item_keys)s) AND l.price IS NOT NULL
        ) priced
        GROUP BY item_key, currency
        """,
        {"item_keys": list(item_keys)},
    ).fetchall()
    return {(r["item_key"], r["currency"]): r["floor"] for r in rows}


def _record_price_drops(conn, crawler_id: int, floors: dict, candidates: list) -> int:
    """Record every candidate price that undercuts its item's floor.

    `floors` must have been read *before* the write these candidates describe
    (see _price_floors), because the floor deliberately includes the price
    being replaced: with no prior price anywhere there is no baseline to beat,
    and a record's first-ever price is not a drop. The comparison is strict, so
    a price that merely holds steady across syncs never re-fires.

    Candidates are `{item_key, url, price, currency}` dicts; duplicates within
    one call are collapsed to the cheapest per (item_key, currency) so a batch
    listing one record twice cannot record two drops for it.
    """
    best: dict = {}
    for candidate in candidates:
        price = candidate.get("price")
        item_key = candidate.get("item_key")
        if price is None or not item_key:
            continue
        key = (item_key, _normalized_currency(candidate.get("currency")))
        if key not in best or price < best[key]["price"]:
            best[key] = candidate

    rows = []
    for (item_key, currency), candidate in best.items():
        floor = floors.get((item_key, currency))
        if floor is None or candidate["price"] >= floor:
            continue
        rows.append((
            item_key, crawler_id, candidate["url"],
            candidate["price"], candidate.get("currency"), floor,
        ))
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO stock_item_price_drops
                (item_key, crawler_id, url, price, currency, previous_best, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            """,
            rows,
        )
    return len(rows)


def delete_expired_price_drops(conn, days: int = PRICE_DROP_RETENTION_DAYS) -> int:
    cur = conn.execute(
        "DELETE FROM stock_item_price_drops "
        "WHERE created_at < CURRENT_TIMESTAMP - make_interval(days => %s)",
        [days],
    )
    return cur.rowcount


def upsert_listing(
    conn,
    release_id: str,
    crawler_id: int,
    url: str,
    price: Optional[float],
    shipping: Optional[float],
    currency: Optional[str],
    condition: Optional[str],
):
    conn.execute(
        """
        INSERT INTO listings (release_id, crawler_id, url, price, shipping, currency, condition, last_checked)
        VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (release_id, crawler_id) DO UPDATE SET
            url = EXCLUDED.url, price = EXCLUDED.price, shipping = EXCLUDED.shipping,
            currency = EXCLUDED.currency, condition = EXCLUDED.condition, last_checked = CURRENT_TIMESTAMP
        """,
        [release_id, crawler_id, url, price, shipping, currency, condition],
    )


def upsert_stock_item_listing(
    conn,
    item_key: str,
    crawler_id: int,
    url: str,
    price: Optional[float],
    shipping: Optional[float],
    currency: Optional[str],
    condition: Optional[str],
):
    # Read before the upsert, not after: the floor a drop has to beat includes
    # the price this call is about to overwrite. See _record_price_drops.
    floors = _price_floors(conn, [item_key])
    conn.execute(
        """
        INSERT INTO listings (item_key, crawler_id, url, price, shipping, currency, condition, last_checked)
        VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (item_key, crawler_id) DO UPDATE SET
            url = EXCLUDED.url, price = EXCLUDED.price, shipping = EXCLUDED.shipping,
            currency = EXCLUDED.currency, condition = EXCLUDED.condition, last_checked = CURRENT_TIMESTAMP
        """,
        [item_key, crawler_id, url, price, shipping, currency, condition],
    )
    _record_price_drops(conn, crawler_id, floors, [
        {"item_key": item_key, "url": url, "price": price, "currency": currency},
    ])


def upsert_stock_item_from_release(conn, release_id: str, crawler_id: int, catalog_release: dict, listing: dict):
    artist = catalog_release["artist"]
    title = catalog_release["title"]
    # Catalog data is already curated Discogs metadata, not scraped text, so
    # unlike replace_stock_items it's stored as-is -- no normalize_artist_casing/
    # normalize_title_casing pass. item_key still hashes the .title()/raw-title
    # legacy convention below, matching replace_stock_items, regardless of what
    # gets stored for display.
    item_key = compute_item_key(catalog_release["artist"].title(), catalog_release["title"], listing["url"])
    # Read before either write below, not after: the floor a drop has to beat
    # includes the price this call is about to overwrite. See _record_price_drops.
    floors = _price_floors(conn, [item_key])
    conn.execute(
        """
        INSERT INTO stock_item_identities (item_key, artist, title, format, last_seen)
        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (item_key) DO UPDATE SET
            artist = EXCLUDED.artist, title = EXCLUDED.title, format = EXCLUDED.format,
            last_seen = CURRENT_TIMESTAMP
        """,
        [item_key, artist, title, catalog_release["format"]],
    )
    conn.execute(
        """
        INSERT INTO stock_items
            (crawler_id, release_id, artist, title, format, price, currency, url, cover_image_url, item_key, last_seen)
        VALUES (%(crawler_id)s, %(release_id)s, %(artist)s, %(title)s, %(format)s, %(price)s, %(currency)s,
                %(url)s, %(cover_image_url)s, %(item_key)s, CURRENT_TIMESTAMP)
        ON CONFLICT (crawler_id, release_id) WHERE release_id IS NOT NULL DO UPDATE SET
            artist = EXCLUDED.artist, title = EXCLUDED.title, format = EXCLUDED.format,
            price = EXCLUDED.price, currency = EXCLUDED.currency, url = EXCLUDED.url,
            cover_image_url = EXCLUDED.cover_image_url, item_key = EXCLUDED.item_key, last_seen = CURRENT_TIMESTAMP
        """,
        {
            "crawler_id": crawler_id, "release_id": release_id, "artist": artist, "title": title,
            "format": catalog_release["format"], "price": listing.get("price"), "currency": listing.get("currency"),
            "url": listing["url"], "cover_image_url": catalog_release["cover_image_url"], "item_key": item_key,
        },
    )
    _record_price_drops(conn, crawler_id, floors, [{
        "item_key": item_key, "url": listing["url"],
        "price": listing.get("price"), "currency": listing.get("currency"),
    }])


def delete_stock_item_for_release(conn, release_id: str, crawler_id: int):
    conn.execute(
        "DELETE FROM stock_items WHERE crawler_id = %s AND release_id = %s",
        [crawler_id, release_id],
    )


def clear_listing_price(conn, release_id: str, crawler_id: int):
    conn.execute(
        "UPDATE listings SET price = NULL, last_checked = CURRENT_TIMESTAMP WHERE release_id = %s AND crawler_id = %s",
        [release_id, crawler_id],
    )


def create_user(conn, discogs_user_id: int, discogs_username: str, invited_by: Optional[int] = None) -> dict:
    return conn.execute(
        """
        INSERT INTO users (discogs_user_id, discogs_username, invited_by, created_at)
        VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
        RETURNING *
        """,
        [discogs_user_id, discogs_username, invited_by],
    ).fetchone()


def create_invite(conn, created_by: int, code: str, note: Optional[str] = None) -> dict:
    return conn.execute(
        """
        INSERT INTO invites (code, created_by, note, created_at)
        VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
        RETURNING *
        """,
        [code, created_by, note],
    ).fetchone()


def list_invites(conn) -> list[dict]:
    return conn.execute(
        """
        SELECT
            invites.code,
            invites.note,
            invites.created_at,
            invites.redeemed_at,
            creator.discogs_username AS created_by_username,
            redeemer.discogs_username AS redeemed_by_username
        FROM invites
        LEFT JOIN users creator ON creator.id = invites.created_by
        LEFT JOIN users redeemer ON redeemer.id = invites.redeemed_by
        ORDER BY invites.created_at DESC
        """
    ).fetchall()


# Includes discogs_oauth_token_encrypted, discogs_oauth_secret_encrypted,
# plaintext plex_token, and plaintext anthropic_api_key — never serialize
# this return value directly into an API response; allow-list fields
# explicitly at the call site.
def get_user_by_discogs_id(conn, discogs_user_id: int) -> Optional[dict]:
    return conn.execute(
        "SELECT * FROM users WHERE discogs_user_id = %s", [discogs_user_id]
    ).fetchone()


def is_user_admin(conn, user_id: int) -> bool:
    row = conn.execute("SELECT is_admin FROM users WHERE id = %s", [user_id]).fetchone()
    return bool(row and row["is_admin"])


def upsert_library_item(
    conn,
    user_id: int,
    discogs_id: str,
    in_collection: Optional[bool] = None,
    in_wishlist: Optional[bool] = None,
    collection_date_added: Optional[str] = None,
    wishlist_date_added: Optional[str] = None,
    price_paid=_UNSET,
):
    # COALESCE resolves "unspecified" (None) to the existing row's own column
    # on update, or FALSE/NULL on first insert — in one atomic statement, so
    # two concurrent partial updates (e.g. collection-sync setting
    # in_collection/collection_date_added, wishlist-sync setting
    # in_wishlist/wishlist_date_added) can't race on a separate read.
    price_set = "" if price_paid is _UNSET else "price_paid = %(price_paid)s,"
    conn.execute(
        f"""
        INSERT INTO library_items (
            user_id, discogs_id, in_collection, in_wishlist,
            collection_date_added, wishlist_date_added, price_paid, last_synced
        )
        VALUES (
            %(user_id)s, %(discogs_id)s, COALESCE(%(in_collection)s, FALSE),
            COALESCE(%(in_wishlist)s, FALSE), %(collection_date_added)s,
            %(wishlist_date_added)s, %(price_paid)s, CURRENT_TIMESTAMP
        )
        ON CONFLICT (user_id, discogs_id) DO UPDATE SET
            in_collection = COALESCE(%(in_collection)s, library_items.in_collection),
            in_wishlist = COALESCE(%(in_wishlist)s, library_items.in_wishlist),
            collection_date_added = COALESCE(%(collection_date_added)s, library_items.collection_date_added),
            wishlist_date_added = COALESCE(%(wishlist_date_added)s, library_items.wishlist_date_added),
            {price_set}
            last_synced = CURRENT_TIMESTAMP
        """,
        {
            "user_id": user_id,
            "discogs_id": discogs_id,
            "in_collection": in_collection,
            "in_wishlist": in_wishlist,
            "collection_date_added": collection_date_added,
            "wishlist_date_added": wishlist_date_added,
            "price_paid": None if price_paid is _UNSET else price_paid,
        },
    )


def get_library_items_for_user(conn, user_id: int) -> list[dict]:
    return conn.execute(
        "SELECT * FROM library_items WHERE user_id = %s", [user_id]
    ).fetchall()


def set_plex_match(conn, user_id: int, discogs_id: str, url: str):
    conn.execute(
        "UPDATE library_items SET plex_url = %s, plex_matched_at = CURRENT_TIMESTAMP "
        "WHERE user_id = %s AND discogs_id = %s",
        [url, user_id, discogs_id],
    )


def clear_plex_match(conn, user_id: int, discogs_id: str):
    conn.execute(
        "UPDATE library_items SET plex_url = NULL, plex_matched_at = NULL "
        "WHERE user_id = %s AND discogs_id = %s",
        [user_id, discogs_id],
    )


def get_library_items_for_plex_match(conn, user_id: int) -> list:
    rows = conn.execute(
        """
        SELECT li.discogs_id, c.artist, c.title
        FROM library_items li JOIN catalog c ON c.discogs_id = li.discogs_id
        WHERE li.user_id = %s AND li.in_collection = TRUE
        """,
        [user_id],
    ).fetchall()
    return [dict(row) for row in rows]


_RELEASE_ALLOWED_SORT = {"artist", "title", "year", "label", "format"}


def get_library_releases(
    conn,
    user_id: int,
    search: Optional[str] = None,
    artist: Optional[str] = None,
    sort: str = "artist",
    order: str = "asc",
    page: int = 1,
    per_page: int = 50,
    release_id: Optional[str] = None,
    scope: Optional[str] = None,
    unmatched: bool = False,
) -> dict:
    order_sql = "DESC" if order.lower() == "desc" else "ASC"
    # Always ASC: NULLs sort last for both ASC and DESC. (The pre-existing
    # `"ASC" if order_sql == "ASC" else "DESC"` formula was a no-op copy of
    # order_sql, which made NULLs sort first on DESC -- the only place that
    # was exercised, the price_<site> sort tests, is deleted in this same
    # change.)
    null_order = "ASC"

    conditions = ["li.user_id = %(user_id)s"]
    params: dict = {"user_id": user_id}

    if release_id:
        conditions.append("c.discogs_id = %(release_id)s")
        params["release_id"] = release_id
    if search:
        # The displayed artist can now differ from the stored one (see
        # canonical_artist_labels' comma-suffix fold below), so a search for
        # what's on screen -- "Beatles, The" -- must also match a row still
        # stored as "The Beatles", not just the raw column.
        conditions.append(
            f"(c.artist ILIKE %(search)s OR {_the_comma_form_sql('c.artist')} ILIKE %(search)s "
            f"OR c.title ILIKE %(search)s)"
        )
        params["search"] = f"%{search}%"
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
    if scope == "discogs":
        conditions.append("li.in_collection = TRUE")
    elif scope == "wishlist":
        conditions.append("li.in_wishlist = TRUE")
    if unmatched:
        conditions.append("li.plex_url IS NULL")

    where = "WHERE " + " AND ".join(conditions)
    base_from = "FROM library_items li JOIN catalog c ON c.discogs_id = li.discogs_id"

    total = conn.execute(f"SELECT COUNT(*) {base_from} {where}", params).fetchone()["count"]

    offset = (page - 1) * per_page
    params["limit"] = per_page
    params["offset"] = offset

    if sort == "discogs_price":
        # Free text with the currency attached -- see _price_sort_sql.
        sort_expr = _price_sort_sql("li.price_paid")
    elif sort == "date_added" and scope in ("discogs", "wishlist"):
        sort_expr = "li." + ("wishlist_date_added" if scope == "wishlist" else "collection_date_added")
    else:
        sort_col = sort if sort in _RELEASE_ALLOWED_SORT else "artist"
        # Case-insensitive so an artist's differently-cased catalog rows stay
        # adjacent even under a byte-ordering collation.
        sort_expr = _artist_sort_sql("c.artist") if sort_col == "artist" else f"c.{sort_col}"

    rows = conn.execute(
        f"""
        SELECT c.*, li.price_paid AS discogs_price, li.plex_url, li.plex_matched_at,
               li.collection_date_added, li.wishlist_date_added
        {base_from} {where}
        ORDER BY CASE WHEN {sort_expr} IS NULL THEN 1 ELSE 0 END {null_order}, {sort_expr} {order_sql}, c.discogs_id
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    ).fetchall()

    releases = []
    for row in rows:
        r = dict(row)
        if scope == "wishlist":
            r["date_added"] = r.pop("wishlist_date_added")
            del r["collection_date_added"]
        elif scope == "discogs":
            r["date_added"] = r.pop("collection_date_added")
            del r["wishlist_date_added"]
        else:
            r["date_added"] = None
            del r["collection_date_added"]
            del r["wishlist_date_added"]
        releases.append(r)
    _apply_canonical_artists(conn, releases)

    return {"total": total, "page": page, "per_page": per_page, "releases": releases}


def get_enabled_crawlers(conn, crawler_type: str = "release") -> list[dict]:
    return conn.execute(
        "SELECT * FROM crawlers WHERE enabled = TRUE AND crawler_type = %s", [crawler_type]
    ).fetchall()


def get_crawlers(conn, crawler_type: str = "release") -> list[dict]:
    return conn.execute("SELECT * FROM crawlers WHERE crawler_type = %s", [crawler_type]).fetchall()


# The dispatch-time replacement for enqueue-time crawler selection: which
# marketplace crawlers should run for one claimed queue row, right now.
#
# is_release distinguishes the two target kinds. requires_discogs_release
# crawlers (Discogs Marketplace) can only search by release id, so they are
# excluded for stock-item targets -- this is the predicate _sync_stock used to
# apply at enqueue time.
#
# pending_crawler_ids narrows the set to a previous pass's unfinished work;
# NULL means no narrowing. The intersection is what makes a crawler disabled
# since that pass drop out silently.
def get_eligible_crawlers(conn, is_release: bool, pending_crawler_ids: Optional[list] = None) -> list[dict]:
    return conn.execute(
        """
        SELECT * FROM crawlers
        WHERE enabled AND crawler_type = 'release'
          AND (%(is_release)s OR NOT requires_discogs_release)
          AND (%(pending)s::int[] IS NULL OR id = ANY(%(pending)s::int[]))
        ORDER BY id
        """,
        {"is_release": is_release, "pending": pending_crawler_ids},
    ).fetchall()


def get_all_crawlers(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM crawlers ORDER BY site_name").fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("_tmp", d["module_path"])
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            d["base_url"] = getattr(mod.Crawler, "base_url", None)
            d["genre_summary"] = getattr(mod.Crawler, "genre_summary", None)
            d["genre"] = getattr(mod.Crawler, "genre", "marketplace")
        except Exception as e:
            # base_url/genre_summary are cosmetic here (they only feed the
            # crawler list), so a broken plugin must not fail the whole
            # listing -- but stay consistent with crawler.py's loader and
            # leave a trace rather than silently reporting None for a
            # plugin that won't import.
            log.warning("Could not load crawler plugin %s for base_url/genre_summary/genre: %s", d["module_path"], e)
            d["base_url"] = None
            d["genre_summary"] = None
            d["genre"] = "marketplace"
        result.append(d)
    return result


def rename_crawler(conn, old_site_name: str, new_site_name: str):
    # register_crawler() upserts ON CONFLICT (site_name), so a plugin's site_name
    # literal can't just be edited in place -- that inserts a new row and orphans
    # the old crawler's id along with its listings/stock_items history. (crawl_queue
    # no longer holds per-crawler history -- a row names no crawler at all.)
    # The NOT EXISTS guard makes this a no-op instead of a unique-constraint crash
    # when new_site_name is already registered (e.g. a prior deploy inserted it
    # directly via register_crawler before this rename step existed).
    conn.execute(
        """
        UPDATE crawlers SET site_name = %s
        WHERE site_name = %s
          AND NOT EXISTS (SELECT 1 FROM crawlers WHERE site_name = %s)
        """,
        [new_site_name, old_site_name, new_site_name],
    )


def register_crawler(conn, site_name: str, module_path: str, crawler_type: str = "release", requires_discogs_release: bool = False):
    conn.execute(
        """
        INSERT INTO crawlers (site_name, module_path, crawler_type, requires_discogs_release, enabled)
        VALUES (%s, %s, %s, %s, TRUE)
        ON CONFLICT (site_name) DO UPDATE SET
            module_path = EXCLUDED.module_path, crawler_type = EXCLUDED.crawler_type,
            requires_discogs_release = EXCLUDED.requires_discogs_release
        """,
        [site_name, module_path, crawler_type, requires_discogs_release],
    )


def set_crawler_enabled(conn, crawler_id: int, enabled: bool):
    conn.execute("UPDATE crawlers SET enabled = %s WHERE id = %s", [enabled, crawler_id])


# Enabling a crawler makes it apply to every target still pending for free --
# eligibility is resolved at dispatch. Targets already marked 'done', though,
# would not see it until the next sync or scheduled sweep, so enabling one
# revives exactly the targets it has no price for.
#
# price IS NOT NULL, not bare NOT EXISTS: clear_listing_price leaves a
# NULL-price row behind for a target this crawler crawled and found nothing
# for, and a bare existence check would read that as already priced. The
# trade-off is that targets where the crawler legitimately found nothing are
# revived on every enable -- bounded and idempotent, but not free.
#
# Release-crawler-only, mirroring get_eligible_crawlers's own crawler_type =
# 'release' filter. A catalog crawler (a store crawler) never writes to
# listings at all -- it writes to stock_items -- so the "no price yet" NOT
# EXISTS check below would be unconditionally true for it, and with no
# release-only clause to narrow it, the UPDATE would match every 'done' row
# in the whole crawl_queue table.
#
# Neither statement touches an 'in_progress' row: the revive filters 'done',
# the widen filters 'pending'. A row a worker is holding right now therefore
# resolved its eligibility from the pre-enable crawler set and is skipped
# here, so its target waits for a later sync or sweep to see this crawler.
# Accepted, not overlooked -- same fallback as a row skipped by the widen's
# SKIP LOCKED, bounded to worker_count x batch_size targets (four by default)
# for the length of one pass. See the design's Risks section for why closing
# it (re-resolving eligibility at resolution time against a claim-time
# baseline) costs more than the delay it removes.
def backfill_crawl_queue_for_crawler(conn, crawler_id: int) -> int:
    requires_release = conn.execute(
        "SELECT requires_discogs_release FROM crawlers WHERE id = %s AND crawler_type = 'release'",
        [crawler_id],
    ).fetchone()
    if requires_release is None:
        return 0
    release_only_clause = (
        "AND crawl_queue.discogs_id IS NOT NULL" if requires_release["requires_discogs_release"] else ""
    )
    # This UPDATE runs unordered across the whole crawl_queue table and can
    # target the same crawl_queue rows a running collection sync is locking in
    # the opposite order (_sync_collection_blocking holds one transaction per
    # page for ~110s, locking rows via enqueue_crawl_queue's ON CONFLICT DO
    # UPDATE). A plain UPDATE would wait on those rows and could be chosen as
    # the deadlock victim -- Postgres decides that, not us, and a real trace
    # showed it picking the sync's side, the opposite of what this backfill is
    # for. Selecting the target ids through FOR UPDATE SKIP LOCKED instead
    # means this transaction never waits on a row lock: a transaction that
    # never waits cannot be part of a wait cycle, so it can neither deadlock
    # nor be picked as a victim. Skipping a row the sync currently holds loses
    # nothing -- the sync is re-enqueuing that same row via enqueue_crawl_
    # queue, which sets it pending with pending_crawler_ids = NULL, meaning
    # "all currently eligible crawlers", so this crawler picks it up at
    # dispatch regardless.
    revived = conn.execute(
        f"""
        UPDATE crawl_queue SET
            status = 'pending', requested_at = CURRENT_TIMESTAMP,
            available_at = CURRENT_TIMESTAMP, claimed_by = NULL, claimed_at = NULL,
            completed_at = NULL, pending_crawler_ids = ARRAY[%(crawler_id)s]
        WHERE id IN (
            SELECT id FROM crawl_queue
            WHERE status = 'done'
              {release_only_clause}
              AND NOT EXISTS (
                  SELECT 1 FROM listings l
                  WHERE l.crawler_id = %(crawler_id)s AND l.price IS NOT NULL
                    AND (l.release_id = crawl_queue.discogs_id OR l.item_key = crawl_queue.item_key)
              )
            FOR UPDATE SKIP LOCKED
        )
        """,
        {"crawler_id": crawler_id},
    ).rowcount
    # A row narrowed by an earlier deferral carries a set that predates this
    # crawler being enabled, so it would otherwise skip it. Rows with NULL need
    # nothing -- NULL already means "all currently eligible". Same SKIP LOCKED
    # shape, but the compensating mechanism is weaker: a skipped row here keeps
    # its narrowed set, so the newly enabled crawler misses that target until
    # the row completes and is re-enqueued. This is bounded and self-healing,
    # the same fallback the enable path relies on when the backfill itself is skipped.
    #
    # available_at is reset along with the widen, and that reset is the point:
    # a narrowed row is narrowed *because* some other crawler is cooling down,
    # so it carries that crawler's cooldown deadline -- up to 30 minutes out.
    # Appending the newly enabled crawler without clearing the deadline would
    # leave the row unclaimable for that whole cooldown, so enabling a crawler
    # would not take effect on the next batch the way it does everywhere else.
    # Dispatch re-defers the still-cooling crawler on its own terms.
    conn.execute(
        """
        UPDATE crawl_queue SET pending_crawler_ids = pending_crawler_ids || %(crawler_id)s,
                               available_at = CURRENT_TIMESTAMP
        WHERE id IN (
            SELECT id FROM crawl_queue
            WHERE status = 'pending' AND pending_crawler_ids IS NOT NULL
              AND NOT (%(crawler_id)s = ANY(pending_crawler_ids))
            FOR UPDATE SKIP LOCKED
        )
        """,
        {"crawler_id": crawler_id},
    )
    return revived


def update_crawler_last_run(conn, crawler_id: int):
    conn.execute("UPDATE crawlers SET last_run = CURRENT_TIMESTAMP WHERE id = %s", [crawler_id])


def _enabled_stock_source_exists(item_key_expr: str) -> str:
    """A stock item is worth crawling only while some enabled crawler still
    lists it. One predicate covers two populations: an item whose store an
    admin disabled, and an item that has left every store's stock --
    replace_stock_items() deletes a crawler's whole batch and reinserts only
    what is currently in stock, so a sold-out item loses its stock_items row
    while its stock_item_identities row and its queue rows survive.

    Release-crawler-sourced stock_items rows (keyed by release_id, written by
    upsert_stock_item_from_release/delete_stock_item_for_release) are a
    separate population this predicate doesn't apply to -- it only reasons
    about the item_key-keyed rows above.

    item_key_expr is always a literal chosen at the call site -- a column
    reference or a bound-parameter placeholder, never request-derived -- the
    same contract _library_match_fragment's user_id_param already carries."""
    return f"""
        EXISTS (
            SELECT 1 FROM stock_items si
            JOIN crawlers sc ON sc.id = si.crawler_id
            WHERE si.item_key = {item_key_expr} AND sc.enabled
        )
    """


# The WHERE on the DO UPDATE is load-bearing, not decorative: re-enqueuing a
# target whose row is already 'pending'/'in_progress' must be a no-op (the
# DO UPDATE runs but its WHERE filters the row out, so in-flight work is left
# untouched), while re-enqueuing a 'done' target must reset it to 'pending' so
# periodic re-crawling of stale listings actually happens -- a plain ON
# CONFLICT DO NOTHING would let a target be crawled exactly once, ever.
#
# Reviving a target clears pending_crawler_ids back to NULL: a re-enqueue means
# "price this target with everything eligible", not "resume whatever narrowed
# set some earlier pass deferred".
#
# There is deliberately no enabled-crawler gate here any more. A queue row
# names no crawler, so there is nothing to gate -- eligibility is resolved at
# dispatch by get_eligible_crawlers() against live crawlers state.
def enqueue_crawl_queue(conn, discogs_id: str):
    conn.execute(
        """
        INSERT INTO crawl_queue (discogs_id) VALUES (%(discogs_id)s)
        ON CONFLICT (discogs_id) DO UPDATE SET
            status = 'pending', requested_at = CURRENT_TIMESTAMP,
            available_at = CURRENT_TIMESTAMP, claimed_by = NULL, claimed_at = NULL,
            completed_at = NULL, pending_crawler_ids = NULL
        WHERE crawl_queue.status = 'done'
        """,
        {"discogs_id": discogs_id},
    )


# Keeps the source gate: it asks whether any enabled *store* still stocks the
# item, which is independent of which marketplace crawler will price it.
def enqueue_crawl_queue_for_stock_item(conn, item_key: str):
    stock_source_gate = _enabled_stock_source_exists("%(item_key)s")
    conn.execute(
        f"""
        INSERT INTO crawl_queue (item_key)
        SELECT %(item_key)s WHERE {stock_source_gate}
        ON CONFLICT (item_key) DO UPDATE SET
            status = 'pending', requested_at = CURRENT_TIMESTAMP,
            available_at = CURRENT_TIMESTAMP, claimed_by = NULL, claimed_at = NULL,
            completed_at = NULL, pending_crawler_ids = NULL
        WHERE crawl_queue.status = 'done'
        """,
        {"item_key": item_key},
    )


# How many rows one claim takes. Small because a batch is now batch_size x
# eligible crawlers of sequential page loads and the whole batch stays
# 'in_progress' for all of it. Named here rather than living only as
# _drain_one_batch's default argument because _queue_stranded_after_seconds
# needs it too: what a claim can legitimately take scales with the number of
# rows in it, and a threshold that assumed one row was shorter than a healthy
# claim.
QUEUE_CLAIM_BATCH_SIZE = 2


# The row lock taken by the inner SELECT ... FOR UPDATE SKIP LOCKED is held
# until the caller commits or rolls back the current transaction -- callers
# must mark_crawl_queue_done() on these rows before/without another worker's
# claim call being able to grab them.
#
# This function's own UPDATE commits immediately -- a short transaction,
# separate from whatever processes the claimed rows afterward (see
# crawl_manager.py's _drain_one_batch) -- so the claim itself is already
# durable by the time this returns: a Machine crash or a genuinely hung
# worker during the processing that follows does not roll it back. That is
# what strands a row: it stays 'in_progress', and this function takes only
# 'pending'. (Cancellation during graceful shutdown is a different case,
# handled separately: _drain_one_batch reverts an in-flight claim on
# cancellation, and shields everything after a successful one, so that path
# never strands anything.)
#
# Such a row used to be unclaimable by anyone else indefinitely.
# reclaim_stranded_crawl_queue_rows now hands it back once the claim has
# outlasted _queue_stranded_after_seconds, and _drain_one_batch calls it in
# the same transaction as this claim -- so the window is bounded by that
# threshold rather than open-ended. Nothing here distinguishes a stranded row
# from one genuinely mid-crawl, and neither does the reclaim: age is the only
# evidence either has.
def claim_crawl_queue_batch(conn, worker_id: str, limit: int) -> list[dict]:
    stock_source_gate = _enabled_stock_source_exists("crawl_queue.item_key")
    return conn.execute(
        f"""
        UPDATE crawl_queue SET status = 'in_progress', claimed_by = %(worker_id)s, claimed_at = CURRENT_TIMESTAMP
        WHERE id IN (
            SELECT id FROM crawl_queue
            WHERE status = 'pending'
              -- Set when a pass deferred a crawler whose site is in
              -- circuit-breaker cooldown; keeps the row out of the claim until
              -- the earliest of those cooldowns expires.
              AND available_at <= CURRENT_TIMESTAMP
              -- Source-side gate: whether anything still stocks the item this
              -- row would price. item_key IS NULL keeps release rows out of it.
              -- There is no crawler-side gate here any more: the row names no
              -- crawler, and _drain_one_batch resolves the eligible set against
              -- live crawlers state per row instead.
              AND (item_key IS NULL OR {stock_source_gate})
            -- (item_key IS NOT NULL) leads the sort so every pending release
            -- row (FALSE) sorts ahead of every pending stock-item row (TRUE),
            -- regardless of which was enqueued first -- a large stock-sync
            -- enqueue burst must never delay a user's own collection crawl
            -- behind it. Priority within one LIMIT'd batch, not exclusion.
            ORDER BY (item_key IS NOT NULL), requested_at, id
            LIMIT %(limit)s
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, discogs_id, item_key, pending_crawler_ids
        """,
        {"worker_id": worker_id, "limit": limit},
    ).fetchall()


# Locks the row for the rest of the caller's transaction, so a result write
# guarded by this cannot be raced by a reclaim landing between the check and the
# write -- reclaim_stranded_crawl_queue_rows selects FOR UPDATE SKIP LOCKED, so
# it skips a row a worker is mid-write on and takes it on a later pass instead.
#
# Needed because the terminal-write gate below is not enough on its own. The
# listing writes run earlier, and they are last-write-wins rather than
# idempotent for a *changing* result: a worker whose claim was reclaimed after
# its search would otherwise overwrite the new claimant's fresher price, or --
# worse -- clear_listing_price a price the new claimant had just found. Refusing
# that worker's terminal write afterwards does not undo either.
def crawl_queue_claim_held(conn, queue_id: int, worker_id: str) -> bool:
    return conn.execute(
        """
        SELECT 1 FROM crawl_queue
        WHERE id = %(queue_id)s AND claimed_by = %(worker_id)s
        FOR UPDATE
        """,
        {"queue_id": queue_id, "worker_id": worker_id},
    ).fetchone() is not None


# Gated on the claim, not just the id. The reclaim made "two workers holding
# one row" reachable -- it cannot tell a dead worker from a slow one, so a pass
# that outruns the stranded threshold has its row handed to someone else while
# it is still crawling. Without this gate the slower worker's terminal write
# lands on top of the new claimant's, and the damaging direction is a stale
# 'done' overwriting a fresh deferral: the deferred crawler is dropped for that
# target until some unrelated later sync revives the row. Returning rowcount
# lets the caller see that its write was ignored and say so.
def mark_crawl_queue_done(conn, queue_id: int, worker_id: str) -> int:
    return conn.execute(
        """
        UPDATE crawl_queue SET status = 'done', completed_at = CURRENT_TIMESTAMP
        WHERE id = %(queue_id)s AND claimed_by = %(worker_id)s
        """,
        {"queue_id": queue_id, "worker_id": worker_id},
    ).rowcount


# The inverse of mark_crawl_queue_done: hands a claimed row back as pending
# with only its unfinished crawlers, deferred until the earliest moment one of
# them is workable again. requested_at is deliberately not touched -- bumping
# it would send a row that merely waited on a cooling-down site to the back of
# the queue behind everything enqueued while it waited.
def defer_crawl_queue_row(conn, queue_id: int, crawler_ids: list, delay_seconds: float, worker_id: str) -> int:
    return conn.execute(
        """
        UPDATE crawl_queue
        SET status = 'pending', claimed_by = NULL, claimed_at = NULL,
            pending_crawler_ids = %(crawler_ids)s,
            available_at = CURRENT_TIMESTAMP + (%(delay_seconds)s * INTERVAL '1 second')
        WHERE id = %(queue_id)s AND claimed_by = %(worker_id)s
        """,
        {
            "queue_id": queue_id, "crawler_ids": list(crawler_ids),
            "delay_seconds": delay_seconds, "worker_id": worker_id,
        },
    ).rowcount


# Undoes claim_crawl_queue_batch for rows the caller never got a chance to
# act on -- e.g. stop_worker_pool()'s task.cancel() landing while the
# claim's thread is still committing. crawl_manager.py's _drain_one_batch
# awaits that claim under asyncio.shield(), which lets the cancellation
# reach the caller immediately while the underlying commit keeps running in
# the background; its except block then reads the already-committed rows
# back from the shielded task and reverts them here before re-raising,
# rather than leaving them orphaned 'in_progress'. Unlike
# defer_crawl_queue_row, this doesn't touch pending_crawler_ids or
# available_at -- nothing about the row's own eligibility state changed,
# only its claim did, so it goes back to exactly as claimable as it was a
# moment before.
def revert_crawl_queue_claim(conn, queue_ids: list):
    if not queue_ids:
        return
    conn.execute(
        "UPDATE crawl_queue SET status = 'pending', claimed_by = NULL, claimed_at = NULL WHERE id = ANY(%(queue_ids)s)",
        {"queue_ids": list(queue_ids)},
    )


# revert_crawl_queue_claim's out-of-process counterpart, and the answer to the
# gap claim_crawl_queue_batch documents above: a row abandoned 'in_progress' by
# a crashed Machine or a hung worker cannot be handed back by that worker,
# because it is gone. Nothing else could reach the row either -- every other
# writer of crawl_queue is gated to 'pending' or 'done' -- so its target was
# frozen out of every future sync, re-enable and schedule. Age is the only
# evidence available without a liveness signal this app does not have.
#
# The cutoff is _queue_stranded_after_seconds and must stay that way. It is the
# same derived value the Queue tab's Stranded tile reports, and it moves when an
# admin enables a crawler or changes the pacing setting; a constant here would
# drift out of agreement with the tile the first time either changed, which is
# worse than either being wrong alone -- that tile is the only instrument an
# operator has for judging whether this reclaim is working. Defined further down
# with the rest of the reporting code and called rather than copied.
#
# Writes exactly the three columns a claim wrote, like revert_crawl_queue_claim.
# pending_crawler_ids in particular is left alone: a row deferred to one crawler
# and then stranded resumes owing that crawler, not every eligible one. Clearing
# it would re-crawl work an earlier pass already paid for. requested_at is left
# alone for defer_crawl_queue_row's reason -- it orders the claim, so bumping it
# would send a row that was stranded for hours behind everything enqueued while
# it was stuck. available_at was already in the past for the row to have been
# claimed at all.
#
# FOR UPDATE SKIP LOCKED rather than a bare UPDATE ... WHERE: every worker on
# every Machine runs this, so a plain UPDATE would have them queue on each
# other's row locks. Skipping a row another worker is already reclaiming loses
# nothing -- that worker is reclaiming it.
def reclaim_stranded_crawl_queue_rows(conn, crawl_delay_seconds: float) -> int:
    stranded_after = _queue_stranded_after_seconds(conn, crawl_delay_seconds)
    return conn.execute(
        """
        UPDATE crawl_queue SET status = 'pending', claimed_by = NULL, claimed_at = NULL
        WHERE id IN (
            SELECT id FROM crawl_queue
            WHERE status = 'in_progress'
              AND claimed_at < CURRENT_TIMESTAMP - %(stranded)s * INTERVAL '1 second'
            FOR UPDATE SKIP LOCKED
        )
        """,
        {"stranded": stranded_after},
    ).rowcount


# Global rather than scoped to one crawler: idempotent, self-correcting, and it
# also clears residue predating the source gate. The cost is that the count a
# disable reports can include rows from another store's delisted items -- it
# means "jobs that are now dead", not "jobs this store created".
#
# 'pending' only. An in_progress row has already been claimed by a worker that
# is mid-crawl and will mark_crawl_queue_done() when it finishes; deleting it
# would leave that UPDATE matching nothing while the crawl still writes its
# listing, so the pair would look never-crawled to the next sync. 'done' rows
# are the record of past crawls and are never re-claimed -- only
# enqueue_crawl_queue_for_stock_item resurrects one, and it now refuses to.
def delete_dead_stock_crawl_queue_rows(conn) -> int:
    stock_source_gate = _enabled_stock_source_exists("crawl_queue.item_key")
    return conn.execute(
        f"""
        DELETE FROM crawl_queue
        WHERE status = 'pending'
          AND item_key IS NOT NULL
          AND NOT {stock_source_gate}
        """
    ).rowcount


# A 'pending' row counts only if something can actually claim it: routers/
# crawl._events_to_replay reads a non-zero count as "this user is mid-job", so
# a count that cannot reach zero would leave the client replaying stale event
# history on every connect. A pending row whose narrowed pending_crawler_ids
# are all disabled -- or any pending row at all when no marketplace crawler is
# enabled -- is unactionable, and is excluded here even though it is still
# 'pending' in the table. _drain_one_batch marks such rows done when it reaches
# them.
#
# That claim holds more weakly for 'in_progress': a row stranded by a crashed
# browser or hung worker is counted here while nothing is actually working on
# it. reclaim_stranded_crawl_queue_rows bounds how long that can last, so the
# count still reaches zero on its own -- but only after the strand ages past
# _queue_stranded_after_seconds and a worker picks the row back up. This
# function has no way to distinguish a stranded in_progress row from one a
# worker is genuinely mid-crawl on, so it counts both.
def count_pending_crawl_queue_for_user(conn, user_id: int) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) FROM crawl_queue cq
        JOIN library_items li ON li.discogs_id = cq.discogs_id
        WHERE li.user_id = %(user_id)s AND cq.status IN ('pending', 'in_progress')
          AND EXISTS (
              SELECT 1 FROM crawlers c
              WHERE c.enabled AND c.crawler_type = 'release'
                AND (cq.pending_crawler_ids IS NULL OR c.id = ANY(cq.pending_crawler_ids))
          )
        """,
        {"user_id": user_id},
    ).fetchone()["count"]


# --- Admin Queue tab -------------------------------------------------------
#
# Read-only reporting over the shared crawl queue. Two units, deliberately kept
# apart everywhere below: a *row* is one target (discogs_id xor item_key) and is
# what the queue's length and its ETA are denominated in; a *work unit* is one
# (row, crawler) pair -- one search a worker will perform -- and is what every
# per-crawler number counts. Units sum to far more than rows, because almost
# every enabled crawler is eligible for almost every row.

QUEUE_ACTIVITY_WINDOW_SECONDS = 3600

# A claim is evidence of a strand only once it has outlasted what the claim
# could legitimately take. A claim covers QUEUE_CLAIM_BATCH_SIZE rows, and each
# row runs one sequential search per eligible crawler, each preceded by a wait
# of 50-100% of crawl_delay_seconds and capped by a page-load timeout -- so its
# honest upper bound scales with the batch size, the pacing setting and how many
# crawlers are enabled. Leaving the batch size out put the threshold below a
# healthy claim on a realistic crawler set. A fixed half hour contradicted the
# rest of this module -- the same fan-out that makes claimed_at useless as a
# completion proxy also means healthy rows routinely stay claimed well past it,
# and they would have lit the Stranded tile, which is coloured critical. The
# floor keeps a tiny or unconfigured deployment from reporting strands within
# seconds; the slack is deliberately generous, because a tile that cries wolf is
# worth less than one that notices late.
#
# This is no longer only a reporting threshold: reclaim_stranded_crawl_queue_rows
# hands a row back at exactly this cutoff, so the generosity now buys something
# concrete. An age-based reclaim cannot tell a dead worker from a slow one, and
# the slack is what keeps it from taking live work away from a pass that is
# merely running long.
QUEUE_STRANDED_FLOOR_SECONDS = 1800
QUEUE_STRANDED_SLACK = 4


def _queue_stranded_after_seconds(conn, crawl_delay_seconds: float) -> float:
    enabled = conn.execute(
        "SELECT COUNT(*) FROM crawlers WHERE enabled AND crawler_type = 'release'"
    ).fetchone()["count"]
    return max(
        QUEUE_STRANDED_FLOOR_SECONDS,
        QUEUE_CLAIM_BATCH_SIZE * enabled * crawl_delay_seconds * QUEUE_STRANDED_SLACK,
    )


def _queue_eligible_crawler_exists(alias: str) -> str:
    """SQL fragment: does any enabled crawler currently resolve for this queue
    row? Mirrors get_eligible_crawlers' WHERE clause exactly. If the two ever
    disagree, the Queue tab reports work no worker will do (or hides work one
    will), so this is written against that function and not re-derived.

    alias is always a literal chosen at the call site -- a table alias, never
    request-derived -- the same contract _enabled_stock_source_exists carries."""
    return f"""
        EXISTS (
            SELECT 1 FROM crawlers c
            WHERE c.enabled AND c.crawler_type = 'release'
              AND ({alias}.discogs_id IS NOT NULL OR NOT c.requires_discogs_release)
              AND ({alias}.pending_crawler_ids IS NULL OR c.id = ANY({alias}.pending_crawler_ids))
        )
    """


# Rows a worker could claim right now, in the same terms claim_crawl_queue_batch
# uses: 'pending', past its available_at, and -- for a stock row -- still listed
# by some enabled store. "Actionable" is the extra condition
# count_pending_crawl_queue_for_user already applies: a row whose narrowed
# pending_crawler_ids are all disabled is still 'pending' in the table but
# nothing will ever run for it, and _drain_one_batch marks it done on sight.
def _queue_row_state_sql() -> str:
    return f"""
        SELECT cq.status,
               cq.discogs_id IS NOT NULL AS is_release,
               cq.claimed_at,
               cq.available_at > CURRENT_TIMESTAMP AS held,
               (cq.item_key IS NULL OR {_enabled_stock_source_exists("cq.item_key")}) AS live,
               {_queue_eligible_crawler_exists("cq")} AS actionable
        FROM crawl_queue cq
        WHERE cq.status <> 'done'
    """


def _queue_totals(conn, stranded_after_seconds: float) -> dict:
    row = conn.execute(
        f"""
        WITH q AS ({_queue_row_state_sql()})
        SELECT
            COUNT(*) FILTER (WHERE status = 'pending' AND live AND actionable AND NOT held) AS claimable_rows,
            COUNT(*) FILTER (WHERE status = 'pending' AND live AND actionable AND NOT held AND is_release) AS claimable_release_rows,
            COUNT(*) FILTER (WHERE status = 'pending' AND live AND actionable AND NOT held AND NOT is_release) AS claimable_stock_rows,
            COUNT(*) FILTER (WHERE status = 'pending' AND live AND actionable AND held) AS held_rows,
            COUNT(*) FILTER (WHERE status = 'pending' AND NOT (live AND actionable)) AS unactionable_rows,
            COUNT(*) FILTER (WHERE status = 'in_progress') AS in_progress_rows,
            COUNT(*) FILTER (WHERE status = 'in_progress'
                             AND claimed_at < CURRENT_TIMESTAMP - %(stranded)s * INTERVAL '1 second') AS stranded_rows
        FROM q
        """,
        {"stranded": stranded_after_seconds},
    ).fetchone()
    return dict(row)


# Off completed_at, the moment the row finished -- not claimed_at, the moment
# its pass began. The two are far apart by design: a row fans out to one
# sequential search per eligible crawler, paced by crawl_delay_seconds, so a
# row claimed well outside this window routinely completes inside it. Counting
# claims instead would report a zero drain rate, and a null ETA, precisely
# while long-running rows were finishing.
def _queue_drain_rate(conn) -> tuple:
    done = conn.execute(
        """
        SELECT COUNT(*) FROM crawl_queue
        WHERE status = 'done' AND completed_at >= CURRENT_TIMESTAMP - %(window)s * INTERVAL '1 second'
        """,
        {"window": QUEUE_ACTIVITY_WINDOW_SECONDS},
    ).fetchone()["count"]
    return done / QUEUE_ACTIVITY_WINDOW_SECONDS, done


_QUEUE_FANOUT_COLUMNS = """
    COUNT(*) AS units,
    MAX(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - cq.requested_at))) AS oldest_seconds,
    COUNT(*) FILTER (WHERE cq.requested_at > CURRENT_TIMESTAMP - INTERVAL '1 hour') AS under_1h,
    COUNT(*) FILTER (WHERE cq.requested_at <= CURRENT_TIMESTAMP - INTERVAL '1 hour'
                       AND cq.requested_at > CURRENT_TIMESTAMP - INTERVAL '24 hours') AS under_24h,
    COUNT(*) FILTER (WHERE cq.requested_at <= CURRENT_TIMESTAMP - INTERVAL '24 hours') AS over_24h
"""


# The fan-out, without ever materializing pending-rows x crawlers. A stock sync
# enqueues rows in numbers that make that cross product large enough to compete
# with the worker pool on every poll of a tab that polls on a timer.
#
# It isn't needed: pending_crawler_ids IS NULL is the common case, and every
# such row resolves to the *same* crawler set for a given target kind. So the
# broad population collapses to a handful of grouped rows whatever the queue's
# size, and only the narrowed minority is grouped per crawler. The cost of that
# split is that only statistics which *compose* across the two halves can be
# reported per crawler -- MIN and bucket counts do, a median does not, which is
# why oldest-plus-buckets is what the tab shows.
def _queue_fanout(conn) -> tuple:
    gate = _enabled_stock_source_exists("cq.item_key")
    broad = conn.execute(
        f"""
        SELECT cq.discogs_id IS NOT NULL AS is_release,
               cq.available_at > CURRENT_TIMESTAMP AS held,
               {_QUEUE_FANOUT_COLUMNS}
        FROM crawl_queue cq
        WHERE cq.status = 'pending' AND cq.pending_crawler_ids IS NULL
          AND (cq.item_key IS NULL OR {gate})
        GROUP BY 1, 2
        """
    ).fetchall()
    narrowed = conn.execute(
        f"""
        SELECT u.cid AS crawler_id,
               cq.discogs_id IS NOT NULL AS is_release,
               cq.available_at > CURRENT_TIMESTAMP AS held,
               {_QUEUE_FANOUT_COLUMNS}
        FROM crawl_queue cq
        CROSS JOIN LATERAL unnest(cq.pending_crawler_ids) AS u(cid)
        WHERE cq.status = 'pending' AND cq.pending_crawler_ids IS NOT NULL
          AND (cq.item_key IS NULL OR {gate})
        GROUP BY 1, 2, 3
        """
    ).fetchall()
    return broad, narrowed


# Distinct listing rows whose last_checked moved -- not searches performed, and
# not "results", since the difference runs in both directions. A release crawl
# that finds nothing still counts: _drain_one_batch calls clear_listing_price,
# which bumps last_checked on the existing row. What touches nothing is a
# first-ever release miss (no row yet for that UPDATE to match, and the "no
# listings pre-population" invariant means none is created) and any stock-item
# miss (the clear path is guarded on is_release). Repeat passes over one
# (target, crawler) inside the window collapse to a single row, because
# upsert_listing updates in place. The caller names it accordingly.
#
# Driven from crawlers rather than grouping over listings, so neither half
# revisits rows outside the window. A bare GROUP BY over listings has no WHERE
# to restrict it -- the all-time MAX forces every row of the table (or of the
# index) to be visited on every poll, and this runs on a 10-second timer. Both
# correlated subqueries ride listings_crawler_last_checked_idx instead: the
# count as a range scan of just the recent slice, the recency as a single
# backward probe of one index entry.
def _queue_crawler_activity(conn) -> dict:
    rows = conn.execute(
        """
        SELECT c.id AS crawler_id,
               (SELECT COUNT(*) FROM listings l
                WHERE l.crawler_id = c.id
                  AND l.last_checked >= CURRENT_TIMESTAMP - %(window)s * INTERVAL '1 second') AS results,
               (SELECT EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - l.last_checked)) FROM listings l
                WHERE l.crawler_id = c.id AND l.last_checked IS NOT NULL
                ORDER BY l.last_checked DESC LIMIT 1) AS last_seconds_ago
        FROM crawlers c
        WHERE c.enabled AND c.crawler_type = 'release'
        """,
        {"window": QUEUE_ACTIVITY_WINDOW_SECONDS},
    ).fetchall()
    return {r["crawler_id"]: r for r in rows}


# Composition and age cover a crawler's whole pending backlog, claimable and
# held alike; only claimable_units/held_units split it. Counting held work out
# of them would empty the composition panel for exactly the crawler an admin
# reached by asking what is held -- a fully held crawler would report a real
# held backlog beside "0 release, 0 stock item" and no oldest wait. The ETA
# does need the claimable stock figure specifically, so that is tracked
# separately rather than by narrowing what stock_units means.
def _queue_accumulate(bucket: dict, agg, is_release: bool, held: bool):
    if held:
        bucket["held_units"] += agg["units"]
    else:
        bucket["claimable_units"] += agg["units"]
        if not is_release:
            bucket["_claimable_stock_units"] += agg["units"]
    bucket["release_units" if is_release else "stock_units"] += agg["units"]
    bucket["age_buckets"]["under_1h"] += agg["under_1h"]
    bucket["age_buckets"]["under_24h"] += agg["under_24h"]
    bucket["age_buckets"]["over_24h"] += agg["over_24h"]
    # Max of the ages, not min of the timestamps: an age composes across the
    # broad/narrowed split the same way, and stays in one type either side of
    # the timestamp/timestamptz line CURRENT_TIMESTAMP sits on.
    oldest = agg["oldest_seconds"]
    if oldest is not None and (bucket["_oldest"] is None or oldest > bucket["_oldest"]):
        bucket["_oldest"] = float(oldest)


def queue_summary(conn, crawl_delay_seconds: float = 30.0) -> dict:
    stranded_after = _queue_stranded_after_seconds(conn, crawl_delay_seconds)
    totals = _queue_totals(conn, stranded_after)
    drain_per_second, rows_done = _queue_drain_rate(conn)
    broad, narrowed = _queue_fanout(conn)
    activity = _queue_crawler_activity(conn)
    in_progress_units = _queue_in_progress_units(conn)
    crawlers = conn.execute(
        """
        SELECT id, site_name, requires_discogs_release FROM crawlers
        WHERE enabled AND crawler_type = 'release' ORDER BY site_name
        """
    ).fetchall()

    out = []
    for crawler in crawlers:
        bucket = {
            "crawler_id": crawler["id"],
            "site_name": crawler["site_name"],
            "requires_discogs_release": crawler["requires_discogs_release"],
            "claimable_units": 0, "held_units": 0, "release_units": 0, "stock_units": 0,
            "in_progress_units": in_progress_units.get(crawler["id"], 0),
            "age_buckets": {"under_1h": 0, "under_24h": 0, "over_24h": 0},
            "_oldest": None, "_claimable_stock_units": 0,
        }
        for agg in broad:
            if not agg["is_release"] and crawler["requires_discogs_release"]:
                continue
            _queue_accumulate(bucket, agg, agg["is_release"], agg["held"])
        for agg in narrowed:
            if agg["crawler_id"] != crawler["id"]:
                continue
            if not agg["is_release"] and crawler["requires_discogs_release"]:
                continue
            _queue_accumulate(bucket, agg, agg["is_release"], agg["held"])

        bucket["oldest_wait_seconds"] = bucket.pop("_oldest")
        claimable_stock = bucket.pop("_claimable_stock_units")
        seen = activity.get(crawler["id"]) or {}
        bucket["results_last_hour"] = seen.get("results") or 0
        last_seen = seen.get("last_seconds_ago")
        bucket["last_result_seconds_ago"] = float(last_seen) if last_seen is not None else None
        bucket["eta_seconds"] = _queue_crawler_eta(bucket, claimable_stock, totals, drain_per_second)
        out.append(bucket)

    totals["rows_done_last_hour"] = rows_done
    totals["eta_seconds"] = (
        totals["claimable_rows"] / drain_per_second if drain_per_second else None
    )
    totals["claimable_units"] = sum(c["claimable_units"] for c in out)
    totals["held_units"] = sum(c["held_units"] for c in out)
    totals["in_progress_units"] = sum(c["in_progress_units"] for c in out)
    return {
        "totals": totals,
        "crawlers": out,
        "stranded_after_seconds": stranded_after,
        "activity_window_seconds": QUEUE_ACTIVITY_WINDOW_SECONDS,
    }


# Units in the rows a worker currently holds -- NOT units still to run. A
# claimed row's unit list is built once by _drain_one_batch and worked through
# in memory; nothing narrows pending_crawler_ids as individual units finish (only
# a deferral rewrites it). So a unit already crawled earlier in the row keeps
# counting until that row resolves, and enabling or disabling a crawler mid-row
# moves this number even though the worker's own list is fixed. Narrowing the
# row per completed unit would put a write on the crawl hot path to sharpen a
# reporting figure, which is the wrong trade; the tab names the segment for what
# this actually measures instead.
#
# In-progress rows are normally few -- a live claim is bounded by workers x
# batch size -- so their fan-out is small enough to resolve the honest way: the
# join the pending population deliberately avoids, which also breaks down per
# crawler while it is there.
#
# That bound is not absolute: a crash strands its claimed rows, and until
# reclaim_stranded_crawl_queue_rows ages them out they sit 'in_progress' and
# this join grows with them. Bounded now rather than monotonic -- strands used
# to accumulate forever -- but a crash loop can still hold a population here
# well above workers x batch size. Left as a join anyway, deliberately.
# Reaching the scale where that matters means thousands of concurrent strands,
# which is a deployment in serious trouble and exactly what the Stranded tile
# exists to shout about -- optimizing this query for that state would be
# optimizing for the case an operator is meant to fix, at the cost of the
# decomposition being harder to follow in the normal one.
def _queue_in_progress_units(conn) -> dict:
    rows = conn.execute(
        """
        SELECT c.id AS crawler_id, COUNT(*) AS units
        FROM crawl_queue cq
        JOIN crawlers c ON c.enabled AND c.crawler_type = 'release'
          AND (cq.discogs_id IS NOT NULL OR NOT c.requires_discogs_release)
          AND (cq.pending_crawler_ids IS NULL OR c.id = ANY(cq.pending_crawler_ids))
        WHERE cq.status = 'in_progress'
        GROUP BY c.id
        """
    ).fetchall()
    return {r["crawler_id"]: r["units"] for r in rows}


# An estimate, and labelled as one. It leans on the one thing the claim order
# guarantees: every claimable release row sorts ahead of every claimable stock
# row. So a crawler that takes no stock work waits only on the release rows,
# and anything else waits on the whole claimable queue. Narrowing is ignored,
# which can only make a crawler's true position earlier than reported.
def _queue_crawler_eta(bucket: dict, claimable_stock_units: int, totals: dict, drain_per_second: float):
    if not drain_per_second or not bucket["claimable_units"]:
        return None
    if claimable_stock_units == 0 and totals["claimable_stock_rows"] > 0:
        position = totals["claimable_release_rows"]
    else:
        position = totals["claimable_rows"]
    return position / drain_per_second


# The next targets this crawler is a candidate for, in claim_crawl_queue_batch's
# own sort order and behind its own gates. Candidates, not a schedule: a row
# past its available_at can still be deferred at dispatch, because
# _drain_one_batch consults the process-local circuit-breaker cooldown that this
# tab deliberately does not expose, and only then moves available_at forward.
# Everything this query can see says the row is claimable; whether the crawler
# actually runs is decided by state living in a worker process.
def queue_next_for_crawler(conn, crawler_id: int, limit: int) -> list:
    gate = _enabled_stock_source_exists("cq.item_key")
    return conn.execute(
        f"""
        SELECT COALESCE(cat.artist, sii.artist) AS artist,
               COALESCE(cat.title, sii.title) AS title,
               CASE WHEN cq.discogs_id IS NOT NULL THEN 'release' ELSE 'stock' END AS kind,
               EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - cq.requested_at)) AS waiting_seconds,
               cq.pending_crawler_ids IS NOT NULL AS narrowed
        FROM crawl_queue cq
        LEFT JOIN catalog cat ON cat.discogs_id = cq.discogs_id
        LEFT JOIN stock_item_identities sii ON sii.item_key = cq.item_key
        WHERE cq.status = 'pending'
          AND cq.available_at <= CURRENT_TIMESTAMP
          AND (cq.item_key IS NULL OR {gate})
          AND (cq.pending_crawler_ids IS NULL OR %(crawler_id)s = ANY(cq.pending_crawler_ids))
          AND EXISTS (
              SELECT 1 FROM crawlers c
              WHERE c.id = %(crawler_id)s AND c.enabled AND c.crawler_type = 'release'
                AND (cq.discogs_id IS NOT NULL OR NOT c.requires_discogs_release)
          )
        ORDER BY (cq.item_key IS NOT NULL), cq.requested_at, cq.id
        LIMIT %(limit)s
        """,
        {"crawler_id": crawler_id, "limit": limit},
    ).fetchall()


def compute_item_key(artist: str, title: str, url: str) -> str:
    return hashlib.sha256(f"{artist}|{title}|{url}".encode()).hexdigest()


def _title_case_words(text: str) -> str:
    # str.title() treats any digit/letter boundary as a new word, mangling
    # names like "13th Floor Elevators" into "13Th Floor Elevators". Walk
    # runs of Unicode letters (str.isalpha(), not an ASCII-only [A-Za-z]
    # regex, which would mishandle accented names like "Björk") and only
    # capitalize a run's first letter when it isn't glued directly to a
    # preceding digit.
    result = []
    in_word = False
    for i, ch in enumerate(text):
        if ch.isalpha():
            if not in_word and not (i > 0 and text[i - 1].isdigit()):
                ch = ch.upper()
            else:
                ch = ch.lower()
            in_word = True
        else:
            in_word = False
        result.append(ch)
    return "".join(result)


def normalize_artist_casing(artist: str) -> str:
    # Title-casing an already-mixed-case name (e.g. "A-100s") mangles it
    # worse than leaving it alone, so only normalize inputs that are all one
    # case to begin with (all-caps "NAILS", all-lowercase "aphex twin").
    if artist.isupper() or artist.islower():
        return _title_case_words(artist)
    return artist


def normalize_title_casing(title: str) -> str:
    # Same rationale as normalize_artist_casing: some stores return release
    # titles in ALL CAPS; only normalize inputs that are all one case to
    # begin with so an already mixed-case title (e.g. "OK Computer") isn't
    # mangled.
    if title.isupper() or title.islower():
        return _title_case_words(title)
    return title


# One casing per artist, whatever each source stored. The most frequent casing
# in the table wins; the tie-break is byte order (COLLATE "C") rather than the
# database's collation, so a two-way tie resolves the same way on every
# deployment -- under en_US the lowercase variant would sort first, under C the
# uppercase one, and the label would depend on how the cluster was initdb'd.
# GROUP BY collapses the duplicates COUNT(*) counts, DISTINCT ON then takes the
# winner per case-folded key.
#
# Every case fold happens here in SQL, and the result is keyed on the caller's
# input string rather than a Python-computed key, because str.lower() and
# LOWER() are not the same function: LOWER() follows the database's collation
# and can't expand one character into two, so on an ordinary en_US cluster
# LOWER('İsis') (U+0130, capital I with dot above) is 'isis' while Python's
# 'İsis'.lower() gives 'i' + U+0307. A Python-side key would match no row there,
# and the label lookup would silently miss -- leaving exactly the split sidebar
# this function exists to fix. (On a tr_TR cluster the two also disagree about
# plain ASCII "ISIS".)
#
# The fold is the database's own LOWER(), locale and all, deliberately: it is
# the same fold used by the artist filters, the artist sort, the expression
# indexes behind them, and the pre-existing owned-artist match in
# _library_match_fragment. A locale-independent fold here alone would put label
# grouping and ownership matching on different definitions of "same artist",
# and the obvious candidate -- folding under COLLATE "C" -- only folds ASCII, so
# "BJÖRK" and "Björk" would stop collapsing. That is a real regression for
# real data, traded against a Turkish-locale cluster nobody deploys this on --
# and on which Turkish folding would be the locally correct answer anyway.
_CANONICAL_ARTIST_SQL = """
    WITH wanted AS (
        SELECT DISTINCT a AS artist FROM unnest(%(artists)s::text[]) AS a
    ),
    grouped AS (
        SELECT LOWER({the_bare}) AS key, artist AS label, COUNT(*) AS n
        FROM {table}
        WHERE LOWER({the_bare}) = ANY (ARRAY(SELECT LOWER({the_bare}) FROM wanted))
        GROUP BY LOWER({the_bare}), artist
    ),
    winner AS (
        SELECT DISTINCT ON (key) key, label FROM grouped
        ORDER BY key, n DESC, label COLLATE "C"
    )
    SELECT w.artist AS input, {the_label} AS label
    FROM wanted w JOIN winner ON winner.key = LOWER({the_w})
"""


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
        WHERE LOWER({the_bare}) = ANY (ARRAY(SELECT LOWER(wanted.artist) || ', the' FROM wanted))
        GROUP BY LOWER({the_bare}), artist
    ),
    winner AS (
        SELECT DISTINCT ON (key) key, label FROM grouped
        ORDER BY key, n DESC, label COLLATE "C"
    )
    SELECT w.artist AS input, {the_label} AS label
    FROM wanted w JOIN winner ON winner.key = LOWER(w.artist) || ', the'
"""


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


def _artist_sort_key(name: str) -> str:
    """Python equivalent of _artist_sort_sql, for the sidebar list which sorts
    in Python (see _canonical_artist_list) rather than SQL. In practice this
    always sees already-comma-folded input (canonical_artist_labels' output),
    so only the suffix branch normally fires -- the prefix branch stays for
    parity with the SQL version and the defensive `labels.get(a, a)` fallback
    in _canonical_artist_list."""
    lower = name.lower()
    if lower.startswith(_ARTIST_SORT_ARTICLE):
        return lower[len(_ARTIST_SORT_ARTICLE):]
    if lower.endswith(_ARTIST_SORT_SUFFIX):
        return lower[: -len(_ARTIST_SORT_SUFFIX)]
    return lower


def _canonical_artist_list(conn, artists) -> list:
    """One entry per artist for a sidebar, canonically cased. Ordering is done
    here rather than in SQL -- the label isn't known until the rows are back --
    so it is Python's ordering, not the database collation's; the two differ
    only for accented and punctuated names. Grouping does not depend on it:
    variants collapse because they share a label, not because they sort
    together."""
    labels = canonical_artist_labels(conn, artists)
    deduped = {labels.get(a, a) for a in artists if a}
    return sorted(deduped, key=lambda a: (_artist_sort_key(a), a.lower(), a))


def _apply_canonical_artists(conn, rows: list[dict]) -> None:
    labels = canonical_artist_labels(conn, [r["artist"] for r in rows])
    for row in rows:
        row["artist"] = labels.get(row["artist"], row["artist"])


def replace_stock_items(conn, crawler_id: int, items: list[dict]) -> list[str]:
    rows = []
    identity_rows = []
    item_keys = []
    candidates = []
    for item in items:
        artist = normalize_artist_casing(item["artist"])
        title = normalize_title_casing(item["title"])
        # item_key keeps hashing the legacy str.title() casing (not the
        # corrected `artist`/`title` above) so existing stock_item_judgments
        # rows, which join on item_key, don't orphan for items whose casing
        # changed here.
        item_key = compute_item_key(item["artist"].title(), item["title"], item["url"])
        item_keys.append(item_key)
        identity_rows.append((item_key, artist, title, item.get("format")))
        rows.append((
            crawler_id, artist, title, item.get("format"), item.get("price"),
            item.get("currency"), item["url"], item.get("cover_image_url"), item_key,
        ))
        candidates.append({
            "item_key": item_key, "url": item["url"],
            "price": item.get("price"), "currency": item.get("currency"),
        })
    # Read before the DELETE below, not after -- which is why the loop above
    # moved ahead of it. The floor a drop has to beat includes the price this
    # call is about to replace, and this path replaces a store's whole batch:
    # reading it afterwards would find no prior price for anything and report
    # the entire catalog as having just got cheaper. See _record_price_drops.
    floors = _price_floors(conn, item_keys)
    conn.execute("DELETE FROM stock_items WHERE crawler_id = %s", [crawler_id])
    if not rows:
        return []
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO stock_item_identities (item_key, artist, title, format, last_seen)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (item_key) DO UPDATE SET
                artist = EXCLUDED.artist, title = EXCLUDED.title, format = EXCLUDED.format,
                last_seen = CURRENT_TIMESTAMP
            """,
            identity_rows,
        )
        cur.executemany(
            """
            INSERT INTO stock_items
                (crawler_id, artist, title, format, price, currency, url, cover_image_url, item_key, last_seen)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            """,
            rows,
        )
    _record_price_drops(conn, crawler_id, floors, candidates)
    return item_keys


_LIBRARY_MEMBERSHIP = {
    "collection": ("in_collection",),
    "wishlist": ("in_wishlist",),
    "all": ("in_collection", "in_wishlist"),
}


def _library_match_fragment(user_id_param: str, library_scope: str) -> str:
    # Callers pass a literal scope; this raises KeyError on an unmapped one.
    # Request-derived scopes go through _in_library_clause instead.
    #
    # The parens around the OR are load-bearing: AND binds tighter, so an
    # unparenthesized multi-column membership would leave the first branch
    # uncorrelated from the stock row and make EXISTS true for every row, and
    # the second branch would drop the li.user_id correlation entirely, so
    # another user's wantlist could match -- RLS on the user_scope connection
    # this always runs under is the only backstop against that.
    membership = " OR ".join(f"li.{col} = TRUE" for col in _LIBRARY_MEMBERSHIP[library_scope])
    # Exact-or-prefix-with-space title match, not exact-only: stock listings
    # often append edition/format qualifiers the catalog title doesn't have
    # (e.g. catalog "Kid A" vs. stock listing "Kid A (Deluxe Reissue)"), so a
    # strict equality would treat an already-owned release as still unowned.
    return f"""FROM library_items li
        JOIN catalog c ON c.discogs_id = li.discogs_id
        WHERE li.user_id = {user_id_param}
          AND ({membership})
          AND LOWER(c.artist) = LOWER(s.artist)
          AND (LOWER(s.title) = LOWER(c.title) OR LOWER(s.title) LIKE LOWER(c.title) || ' %%')"""


def _in_library_clause(user_id_param: str, library_scope: Optional[str]) -> Optional[str]:
    """EXISTS clause for 'this stock row is in the user's library at the given
    scope', or None when the scope doesn't filter. Normalizing here rather than
    per-caller keeps the allow-set gate in one place."""
    if library_scope not in _LIBRARY_MEMBERSHIP:
        return None
    return f"EXISTS (SELECT 1 {_library_match_fragment(user_id_param, library_scope)})"


# Recommended items are defined as ones the user doesn't already own. A release
# merely on the wantlist stays recommendable, so this stays collection-scoped.
def _not_owned_clause(user_id_param: str) -> str:
    return f"NOT EXISTS (SELECT 1 {_library_match_fragment(user_id_param, 'collection')})"


def _collection_artist_clause(user_id_param: str) -> str:
    """EXISTS clause for "this stock row's artist is one the user collects" --
    some release by that artist sits in their Discogs collection, whether or
    not this particular record does.

    Deliberately artist-level, and deliberately not `_library_match_fragment`
    with the title half made optional: that fragment answers the release-level
    question the Track tab's Collection filter already asks, and a flag on it
    would read as two spellings of one idea rather than the two different
    questions they are. This one is the complement -- the rest of the shelf by
    artists already on it, most of which the user does not own.

    Matching is on `_artist_sort_sql`'s bare, article-stripped key rather than
    `_library_match_fragment`'s plain `LOWER(artist)`: a store's spelling and
    Discogs' spelling of one artist routinely disagree over "The X" vs
    "X, The" vs bare "X", and at artist granularity that disagreement decides
    whether the artist appears at all rather than merely which of their
    records match. It also keeps this filter agreeing with the artist sidebar
    beside it, whose own equality filter compares the same key. Both sides
    have an expression index on exactly that key (`catalog_artist_bare_lower_idx`,
    `stock_items_artist_bare_lower_idx`).
    """
    return f"""EXISTS (
            SELECT 1
            FROM library_items li
            JOIN catalog c ON c.discogs_id = li.discogs_id
            WHERE li.user_id = {user_id_param}
              AND li.in_collection = TRUE
              AND {_artist_sort_sql('c.artist')} = {_artist_sort_sql('s.artist')}
        )"""


_STOCK_ALLOWED_SORT = {"artist", "title", "format", "price"}


def get_stock_items(
    conn,
    user_id: int,
    search: Optional[str] = None,
    artist: Optional[str] = None,
    sort: str = "artist",
    order: str = "asc",
    page: int = 1,
    per_page: int = 50,
    library_scope: Optional[str] = None,
    recommended: bool = False,
    saved_only: bool = False,
    overlapped_artists: bool = False,
    exclude_crawler_ids: Optional[list[int]] = None,
) -> dict:
    order_sql = "DESC" if order.lower() == "desc" else "ASC"
    # The sort expression is collection-pinned, so it only applies to a scope
    # that can contain rows carrying a collection price. Under wishlist scope
    # every key would be NULL, leaving all rows tied and pagination unstable.
    # The lookup's default covers None and any unmapped scope.
    if sort == "discogs_price" and "in_collection" in _LIBRARY_MEMBERSHIP.get(library_scope, ()):
        sort_expr = "(SELECT {price} {match} LIMIT 1)".format(
            price=_price_sort_sql("li.price_paid"),
            match=_library_match_fragment("%(user_id)s", "collection"),
        )
    elif sort == "source":
        sort_expr = "cr.site_name"
    else:
        sort_col = sort if sort in _STOCK_ALLOWED_SORT else "artist"
        # See get_library_releases: keeps casing variants of one artist together.
        sort_expr = _artist_sort_sql("s.artist") if sort_col == "artist" else f"s.{sort_col}"

    conditions = []
    params: dict = {"user_id": user_id}
    if search:
        # See the matching comment in get_library_releases: search must also
        # match the comma-folded display form, not just the stored spelling.
        conditions.append(
            f"(s.artist ILIKE %(search)s OR {_the_comma_form_sql('s.artist')} ILIKE %(search)s "
            f"OR s.title ILIKE %(search)s)"
        )
        params["search"] = f"%{search}%"
    if artist:
        # See the matching clause in get_library_releases: the filter value is
        # a canonical label, not any one store's spelling -- including, now,
        # a spelling with no article at all.
        conditions.append(
            f"{_artist_sort_sql('s.artist')} = {_artist_sort_sql('%(artist)s')}"
        )
        params["artist"] = artist
    in_library = _in_library_clause("%(user_id)s", library_scope)
    if in_library:
        conditions.append(in_library)
    if overlapped_artists:
        conditions.append(_collection_artist_clause("%(user_id)s"))
    if recommended:
        conditions.append(
            "s.item_key IN (SELECT item_key FROM stock_item_judgments "
            "WHERE user_id = %(user_id)s AND recommended = TRUE)"
        )
        conditions.append(_not_owned_clause("%(user_id)s"))
    if saved_only:
        conditions.append(
            "s.item_key IN (SELECT item_key FROM stock_item_saves "
            "WHERE user_id = %(user_id)s)"
        )
    if exclude_crawler_ids:
        conditions.append("s.crawler_id != ALL(%(exclude_crawler_ids)s)")
        params["exclude_crawler_ids"] = exclude_crawler_ids
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total = conn.execute(f"SELECT COUNT(*) FROM stock_items s {where}", params).fetchone()["count"]

    offset = (page - 1) * per_page
    params["limit"] = per_page
    params["offset"] = offset
    # Always ASC: NULLs sort last for both ASC and DESC, matching
    # get_library_releases. The `"ASC" if order_sql == "ASC" else "DESC"`
    # formula this replaces was a no-op copy of order_sql, so a descending
    # sort ordered the CASE guard descending too and put its 1s -- the rows
    # with no sort key -- first. Reachable on every stock sort with a nullable
    # key: an unpriced Cost, an absent Format, and the discogs_price sort
    # whose "N/A"/blank values this branch's numeric extraction maps to NULL.
    null_order = "ASC"
    rows = conn.execute(
        f"""
        SELECT s.id, s.artist, s.title, s.format, s.price, s.currency, s.url, s.cover_image_url, s.last_seen,
               s.item_key, cr.site_name AS source, j.reason AS reason,
               (sv.item_key IS NOT NULL) AS saved,
               (SELECT li.price_paid {_library_match_fragment('%(user_id)s', 'collection')} LIMIT 1) AS discogs_price
        FROM stock_items s
        JOIN crawlers cr ON cr.id = s.crawler_id
        LEFT JOIN stock_item_judgments j ON j.item_key = s.item_key AND j.user_id = %(user_id)s
        LEFT JOIN stock_item_saves sv ON sv.item_key = s.item_key AND sv.user_id = %(user_id)s
        {where}
        ORDER BY CASE WHEN {sort_expr} IS NULL THEN 1 ELSE 0 END {null_order}, {sort_expr} {order_sql}, s.id
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    ).fetchall()

    item_keys = [r["item_key"] for r in rows]
    comparison_sql = """
        SELECT l.item_key, l.price, l.currency, l.url, l.condition, l.last_checked, cr.site_name AS source
        FROM listings l
        JOIN crawlers cr ON cr.id = l.crawler_id
        WHERE l.item_key = ANY(%(item_keys)s) AND l.price IS NOT NULL
    """
    comparison_params = {"item_keys": item_keys}
    if exclude_crawler_ids:
        comparison_sql += " AND cr.id != ALL(%(exclude_crawler_ids)s)"
        comparison_params["exclude_crawler_ids"] = exclude_crawler_ids
    comparison_sql += " ORDER BY l.item_key, cr.site_name"
    comparisons_by_item: dict[str, list[dict]] = {}
    for c in conn.execute(comparison_sql, comparison_params).fetchall():
        comparisons_by_item.setdefault(c["item_key"], []).append(c)

    items = []
    own_rows = [dict(row) for row in rows]
    # Before the loop, so the comparison rows below -- which take their artist
    # from the own row -- inherit the canonical label too.
    _apply_canonical_artists(conn, own_rows)
    for r in own_rows:
        items.append({**r, "is_own": True})
        for c in comparisons_by_item.get(r["item_key"], []):
            items.append({
                "id": f"{r['id']}:{c['source']}",
                "item_key": r["item_key"], "artist": r["artist"], "title": r["title"],
                "format": r["format"], "cover_image_url": r["cover_image_url"],
                "discogs_price": r["discogs_price"], "saved": r["saved"],
                "price": c["price"], "currency": c["currency"], "url": c["url"],
                "source": c["source"], "reason": r["reason"], "last_seen": c["last_checked"],
                "is_own": False,
            })

    return {"total": total, "page": page, "per_page": per_page, "items": items}


def get_distinct_stock_artists(conn, user_id: int, library_scope: Optional[str] = None, recommended: bool = False,
    saved_only: bool = False,
    overlapped_artists: bool = False,
    exclude_crawler_ids: Optional[list[int]] = None,
) -> list[str]:
    conditions = []
    params: dict = {"user_id": user_id}
    in_library = _in_library_clause("%(user_id)s", library_scope)
    if in_library:
        conditions.append(in_library)
    if overlapped_artists:
        conditions.append(_collection_artist_clause("%(user_id)s"))
    if recommended:
        conditions.append(
            "s.item_key IN (SELECT item_key FROM stock_item_judgments "
            "WHERE user_id = %(user_id)s AND recommended = TRUE)"
        )
        conditions.append(_not_owned_clause("%(user_id)s"))
    if saved_only:
        conditions.append(
            "s.item_key IN (SELECT item_key FROM stock_item_saves "
            "WHERE user_id = %(user_id)s)"
        )
    if exclude_crawler_ids:
        conditions.append("s.crawler_id != ALL(%(exclude_crawler_ids)s)")
        params["exclude_crawler_ids"] = exclude_crawler_ids
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = conn.execute(f"SELECT DISTINCT s.artist FROM stock_items s {where}", params).fetchall()
    return _canonical_artist_list(conn, [row["artist"] for row in rows])


def get_unjudged_stock_items(conn, user_id: int, limit: int) -> list[dict]:
    limit_clause = "LIMIT %(limit)s" if limit > 0 else ""
    rows = conn.execute(
        f"""
        SELECT s.item_key, s.artist, s.title
        FROM stock_items s
        LEFT JOIN stock_item_judgments j ON j.item_key = s.item_key AND j.user_id = %(user_id)s
        WHERE j.item_key IS NULL
          AND {_not_owned_clause('%(user_id)s')}
        GROUP BY s.item_key, s.artist, s.title
        ORDER BY MIN(s.last_seen) ASC
        {limit_clause}
        """,
        {"user_id": user_id, "limit": limit},
    ).fetchall()
    return rows


def count_unjudged_stock_items(conn, user_id: int) -> int:
    return conn.execute(
        f"""
        SELECT COUNT(DISTINCT s.item_key) FROM stock_items s
        LEFT JOIN stock_item_judgments j ON j.item_key = s.item_key AND j.user_id = %(user_id)s
        WHERE j.item_key IS NULL
          AND {_not_owned_clause('%(user_id)s')}
        """,
        {"user_id": user_id},
    ).fetchone()["count"]


def get_taste_listing(conn, user_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT c.artist, c.title
        FROM library_items li
        JOIN catalog c ON c.discogs_id = li.discogs_id
        WHERE li.user_id = %s AND (li.in_collection = TRUE OR li.in_wishlist = TRUE)
        ORDER BY c.artist, c.title
        """,
        [user_id],
    ).fetchall()
    return [f"{row['artist']} - {row['title']}" for row in rows]


def upsert_stock_judgments(conn, user_id: int, judgments: list[dict]):
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO stock_item_judgments (user_id, item_key, recommended, reason, judged_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id, item_key) DO UPDATE SET
                recommended = EXCLUDED.recommended, reason = EXCLUDED.reason, judged_at = CURRENT_TIMESTAMP
            """,
            [(user_id, j["item_key"], j["recommended"], j.get("reason")) for j in judgments],
        )


def save_stock_item(conn, user_id: int, item_key: str) -> None:
    conn.execute(
        """
        INSERT INTO stock_item_saves (user_id, item_key)
        VALUES (%s, %s)
        ON CONFLICT (user_id, item_key) DO NOTHING
        """,
        [user_id, item_key],
    )


def unsave_stock_item(conn, user_id: int, item_key: str) -> None:
    conn.execute(
        "DELETE FROM stock_item_saves WHERE user_id = %s AND item_key = %s",
        [user_id, item_key],
    )


# The whole of "which price drops is this user notified about", in one place so
# the list, the unread count and the read watermark cannot drift apart.
#
# stock_item_price_drops is a global table -- see its schema comment -- and this
# join through the caller's own stock_item_saves rows is what makes it per-user.
# RLS on stock_item_saves does the isolating, exactly as it does on every other
# read path, so a user can only ever be shown drops for items they saved
# themselves. Always run under db.user_scope().
#
# created_at >= saved_at is what makes "notification" the right word: you are
# told about changes since you started watching, not handed a record's back
# catalogue of price history the moment you bookmark it.
# Ends on a join condition rather than a WHERE clause so callers can append
# joins of their own (the list query needs the item's identity and its source's
# name) without having to restate the visibility rule.
_SAVED_PRICE_DROPS_SQL = """
    FROM stock_item_price_drops d
    JOIN stock_item_saves sv
      ON sv.item_key = d.item_key
     AND sv.user_id = %(user_id)s
     AND d.created_at >= sv.saved_at
"""


def get_price_drop_notifications(conn, user_id: int, limit: int = 50) -> list:
    return conn.execute(
        f"""
        SELECT d.id, d.item_key, d.url, d.price, d.currency, d.previous_best, d.created_at,
               i.artist, i.title, i.format, cr.site_name AS source,
               (SELECT s.cover_image_url FROM stock_items s
                 WHERE s.item_key = d.item_key AND s.cover_image_url IS NOT NULL
                 LIMIT 1) AS cover_image_url
        {_SAVED_PRICE_DROPS_SQL}
        JOIN stock_item_identities i ON i.item_key = d.item_key
        JOIN crawlers cr ON cr.id = d.crawler_id
        ORDER BY d.id DESC
        LIMIT %(limit)s
        """,
        {"user_id": user_id, "limit": limit},
    ).fetchall()


def count_unread_price_drops(conn, user_id: int) -> int:
    return conn.execute(
        f"""
        SELECT COUNT(*) {_SAVED_PRICE_DROPS_SQL}
        WHERE d.id > COALESCE(
              (SELECT last_read_drop_id FROM user_notification_reads WHERE user_id = %(user_id)s), 0)
        """,
        {"user_id": user_id},
    ).fetchone()["count"]


def get_notification_watermark(conn, user_id: int) -> int:
    """The highest drop id this user has already seen. Zero when they have
    never opened the tab, which is what makes every existing drop unread."""
    row = conn.execute(
        "SELECT last_read_drop_id FROM user_notification_reads WHERE user_id = %s",
        [user_id],
    ).fetchone()
    return row["last_read_drop_id"] if row else 0


def latest_price_drop_id(conn, user_id: int) -> Optional[int]:
    return conn.execute(
        f"SELECT MAX(d.id) AS latest {_SAVED_PRICE_DROPS_SQL}",
        {"user_id": user_id},
    ).fetchone()["latest"]


def mark_price_drops_read(conn, user_id: int, up_to_id: int) -> None:
    """GREATEST, not a plain assignment: two tabs open on the Notifications
    view will each POST their own watermark, and the one that started earlier
    can land last. Taking the maximum means a stale request can never un-read
    something newer."""
    conn.execute(
        """
        INSERT INTO user_notification_reads (user_id, last_read_drop_id, updated_at)
        VALUES (%s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (user_id) DO UPDATE SET
            last_read_drop_id = GREATEST(user_notification_reads.last_read_drop_id, EXCLUDED.last_read_drop_id),
            updated_at = CURRENT_TIMESTAMP
        """,
        [user_id, up_to_id],
    )


def has_any_price_paid(conn, user_id: int) -> bool:
    return conn.execute(
        "SELECT EXISTS(SELECT 1 FROM library_items WHERE user_id = %s "
        "AND in_collection = TRUE AND price_paid IS NOT NULL)",
        [user_id],
    ).fetchone()["exists"]


def has_any_stock_judgment(conn, user_id: int) -> bool:
    return conn.execute(
        "SELECT EXISTS(SELECT 1 FROM stock_item_judgments WHERE user_id = %s)", [user_id]
    ).fetchone()["exists"]


def clear_stock_judgments(conn, user_id: int) -> int:
    cursor = conn.execute("DELETE FROM stock_item_judgments WHERE user_id = %s", [user_id])
    return cursor.rowcount


def get_hidden_crawler_ids(conn, user_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT crawler_id FROM user_hidden_crawlers WHERE user_id = %s", [user_id]
    ).fetchall()
    return [row["crawler_id"] for row in rows]


def set_hidden_crawler_ids(conn, user_id: int, crawler_ids: list[int]):
    conn.execute("DELETE FROM user_hidden_crawlers WHERE user_id = %s", [user_id])
    unique_ids = list(dict.fromkeys(crawler_ids))
    if unique_ids:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO user_hidden_crawlers (user_id, crawler_id) "
                "SELECT %s, %s WHERE EXISTS (SELECT 1 FROM crawlers WHERE id = %s)",
                [(user_id, cid, cid) for cid in unique_ids],
            )


def get_recommended_stock_items(conn, user_id: int) -> list[dict]:
    # DISTINCT ON (s.item_key) is load-bearing, not decorative: item_key is
    # not unique in stock_items (the same artist/title/url can be seen by more
    # than one crawler, or duplicated within one replace_stock_items() call,
    # which has no ON CONFLICT on item_key), so a plain join here would
    # surface the same recommendation more than once for one judgment row.
    return conn.execute(
        f"""
        SELECT artist, title, format, price, source, url, reason FROM (
            SELECT DISTINCT ON (s.item_key)
                s.item_key, s.artist, s.title, s.format, s.price, cr.site_name AS source, s.url,
                j.reason AS reason
            FROM stock_items s
            JOIN crawlers cr ON cr.id = s.crawler_id
            JOIN stock_item_judgments j ON j.item_key = s.item_key AND j.user_id = %(user_id)s
            WHERE j.recommended = TRUE
              AND {_not_owned_clause('%(user_id)s')}
            ORDER BY s.item_key, s.last_seen DESC
        ) deduped
        ORDER BY artist, title
        """,
        {"user_id": user_id},
    ).fetchall()


def get_all_stock_judgments(conn, user_id: int) -> list[dict]:
    # Drives FROM the judgments table, not from stock_items, so a judgment
    # whose item isn't currently in stock -- or was never crawled here at
    # all, having arrived by import -- still comes out. That's the whole
    # point: the file is a backup of what was paid for, not a shopping list.
    #
    # stock_item_identities is the durable artist/title/format source (only
    # ever upserted); stock_items is deleted and reinserted per crawler on
    # every sync, so it can only supply the live price/source/link.
    #
    # LEFT JOIN LATERAL ... LIMIT 1 replaces the DISTINCT ON that
    # get_recommended_stock_items needs: item_key is not unique in
    # stock_items, so an unguarded join would emit one row per crawler that
    # saw the item. last_seen alone doesn't break ties deterministically --
    # replace_stock_items stamps it as CURRENT_TIMESTAMP per transaction, so
    # two crawlers' rows for the same item_key can carry the same value --
    # so site_name is added as a tiebreaker to keep the export byte-for-byte
    # stable across runs, which matters for a file whose job is round-trip.
    return conn.execute(
        """
        SELECT
            COALESCE(i.artist, '') AS artist,
            COALESCE(i.title, '')  AS title,
            COALESCE(i.format, '') AS format,
            d.price, d.currency, d.source, d.url,
            j.reason, j.item_key, j.recommended, j.judged_at
        FROM stock_item_judgments j
        LEFT JOIN stock_item_identities i ON i.item_key = j.item_key
        LEFT JOIN LATERAL (
            SELECT s.price, s.currency, cr.site_name AS source, s.url
            FROM stock_items s
            JOIN crawlers cr ON cr.id = s.crawler_id
            WHERE s.item_key = j.item_key
            ORDER BY s.last_seen DESC, cr.site_name
            LIMIT 1
        ) d ON TRUE
        WHERE j.user_id = %(user_id)s
        ORDER BY COALESCE(i.artist, ''), COALESCE(i.title, ''), j.item_key
        """,
        {"user_id": user_id},
    ).fetchall()


def import_stock_judgments(conn, user_id: int, judgments: list[dict]) -> tuple[int, int, list[str]]:
    """Upsert imported judgments, newest judged_at winning. Returns
    (inserted, updated, applied_keys); rows whose local judgment is already
    at least as new are neither, are the caller's `unchanged`, and their
    keys are excluded from applied_keys.

    Deliberately not upsert_stock_judgments: that one stamps
    judged_at = CURRENT_TIMESTAMP, which would erase the imported timestamps,
    break newest-wins on the next round-trip, and make every imported row
    look freshly judged.

    Callers must have collapsed duplicate item_keys first -- Postgres rejects
    a statement whose ON CONFLICT target appears twice.
    """
    if not judgments:
        return (0, 0, [])
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
        RETURNING (xmax = 0) AS inserted, item_key
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
    applied_keys = [r["item_key"] for r in rows]
    return (inserted, len(rows) - inserted, applied_keys)


def count_matching_stock_items(conn, item_keys: list[str]) -> int:
    """How many of these keys are in stock right now. Callers must pass only
    the keys an import actually applied, not the whole parsed file -- the
    Recommended filter inner-joins stock_items, so this is what decides
    whether an import changes anything the user can see yet.
    """
    if not item_keys:
        return 0
    return conn.execute(
        "SELECT COUNT(DISTINCT item_key) FROM stock_items WHERE item_key = ANY(%(keys)s)",
        {"keys": item_keys},
    ).fetchone()["count"]


def get_missing_releases(conn, user_id: int) -> list[str]:
    # Scoped to crawler_type = 'release': only release crawlers ever write a
    # listings row (via upsert_listing), so counting catalog crawlers in the
    # denominator would make every release permanently short of the target
    # and thus permanently "missing".
    enabled_count = conn.execute(
        "SELECT COUNT(*) FROM crawlers WHERE enabled = TRUE AND crawler_type = 'release'"
    ).fetchone()["count"]
    if enabled_count == 0:
        return []
    rows = conn.execute(
        """
        SELECT li.discogs_id FROM library_items li
        WHERE li.user_id = %(user_id)s AND (
            SELECT COUNT(DISTINCT l.crawler_id) FROM listings l
            JOIN crawlers c ON c.id = l.crawler_id AND c.enabled = TRUE AND c.crawler_type = 'release'
            WHERE l.release_id = li.discogs_id AND l.price IS NOT NULL
        ) < %(enabled_count)s
        """,
        {"user_id": user_id, "enabled_count": enabled_count},
    ).fetchall()
    return [row["discogs_id"] for row in rows]


def get_crawl_status_for_user(conn, user_id: int) -> dict:
    total = conn.execute(
        "SELECT COUNT(*) FROM library_items WHERE user_id = %s", [user_id]
    ).fetchone()["count"]
    # Scoped to crawler_type = 'release' -- see get_missing_releases, whose
    # mode=missing candidate set this status must stay consistent with.
    enabled_count = conn.execute(
        "SELECT COUNT(*) FROM crawlers WHERE enabled = TRUE AND crawler_type = 'release'"
    ).fetchone()["count"]

    if enabled_count == 0 or total == 0:
        return {"total": total, "missing": total, "oldest_checked": None}

    complete = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT li.discogs_id
            FROM library_items li
            JOIN listings l ON l.release_id = li.discogs_id
            JOIN crawlers c ON c.id = l.crawler_id AND c.enabled = TRUE AND c.crawler_type = 'release'
            WHERE li.user_id = %(user_id)s AND l.price IS NOT NULL
            GROUP BY li.discogs_id
            HAVING COUNT(DISTINCT l.crawler_id) = %(enabled_count)s
        ) complete_releases
        """,
        {"user_id": user_id, "enabled_count": enabled_count},
    ).fetchone()["count"]

    oldest = conn.execute(
        """
        SELECT MIN(l.last_checked) FROM listings l
        JOIN library_items li ON li.discogs_id = l.release_id
        WHERE li.user_id = %s
        """,
        [user_id],
    ).fetchone()["min"]

    return {"total": total, "missing": total - complete, "oldest_checked": oldest}


# Callers must call this before delete_orphaned_releases in the same sync
# pass. delete_orphaned_releases only removes rows with both in_collection
# and in_wishlist FALSE; a release just dropped from the wantlist still has
# in_wishlist = TRUE until this runs, so calling delete first would leave it
# stranded (undeleted) for a full extra sync cycle instead of being cleaned
# up in this one.
def clear_wishlist_flags_not_in(conn, user_id: int, seen_ids: set) -> int:
    cursor = conn.execute(
        "UPDATE library_items SET in_wishlist = FALSE WHERE user_id = %s AND in_wishlist = TRUE AND discogs_id != ALL(%s)",
        [user_id, list(seen_ids)],
    )
    return cursor.rowcount


# Must run after clear_wishlist_flags_not_in in the same sync pass -- see its
# docstring comment for why the order matters.
def delete_orphaned_releases(conn, user_id: int) -> list[str]:
    rows = conn.execute(
        """
        DELETE FROM library_items
        WHERE user_id = %s AND in_collection = FALSE AND in_wishlist = FALSE
        RETURNING discogs_id
        """,
        [user_id],
    ).fetchall()
    return [row["discogs_id"] for row in rows]


def get_distinct_artists(conn, user_id: int, scope: Optional[str] = None) -> list[str]:
    conditions = ["li.user_id = %(user_id)s"]
    if scope == "discogs":
        conditions.append("li.in_collection = TRUE")
    elif scope == "wishlist":
        conditions.append("li.in_wishlist = TRUE")
    where = "WHERE " + " AND ".join(conditions)
    rows = conn.execute(
        f"""
        SELECT DISTINCT c.artist FROM library_items li
        JOIN catalog c ON c.discogs_id = li.discogs_id
        {where}
        """,
        {"user_id": user_id},
    ).fetchall()
    return _canonical_artist_list(conn, [row["artist"] for row in rows])


def create_oauth_request_state(conn, request_token: str, request_token_secret: str):
    conn.execute(
        "INSERT INTO oauth_request_state (request_token, request_token_secret) VALUES (%s, %s)",
        [request_token, request_token_secret],
    )


def get_and_delete_oauth_request_state(conn, request_token: str, max_age_minutes: int = 10) -> Optional[dict]:
    # Validity is computed in the RETURNING clause itself (created_at vs
    # Postgres's own NOW()), not compared against Python's clock afterward —
    # created_at is a server-computed, zoneless TIMESTAMP, and comparing it to
    # datetime.utcnow() would silently assume the Postgres session TimeZone
    # GUC is UTC, which nothing here pins.
    row = conn.execute(
        """
        DELETE FROM oauth_request_state WHERE request_token = %(request_token)s
        RETURNING request_token_secret,
                  created_at > NOW() - (%(max_age_minutes)s || ' minutes')::interval AS is_valid
        """,
        {"request_token": request_token, "max_age_minutes": max_age_minutes},
    ).fetchone()
    if row is None or not row["is_valid"]:
        return None
    return row


# Callers must Fernet-encrypt oauth_token_encrypted/oauth_secret_encrypted
# before calling — this function does not enforce or perform encryption.
def create_pending_signup(
    conn,
    signup_token: str,
    discogs_user_id: int,
    discogs_username: str,
    oauth_token_encrypted: bytes,
    oauth_secret_encrypted: bytes,
):
    conn.execute(
        """
        INSERT INTO pending_signups
            (signup_token, discogs_user_id, discogs_username, oauth_token_encrypted, oauth_secret_encrypted)
        VALUES (%s, %s, %s, %s, %s)
        """,
        [signup_token, discogs_user_id, discogs_username, oauth_token_encrypted, oauth_secret_encrypted],
    )


def get_and_delete_pending_signup(conn, signup_token: str, max_age_minutes: int = 15) -> Optional[dict]:
    # See get_and_delete_oauth_request_state: validity is computed inside
    # Postgres's own RETURNING clause, not against Python's clock.
    row = conn.execute(
        """
        DELETE FROM pending_signups WHERE signup_token = %(signup_token)s
        RETURNING discogs_user_id, discogs_username, oauth_token_encrypted, oauth_secret_encrypted,
                  created_at > NOW() - (%(max_age_minutes)s || ' minutes')::interval AS is_valid
        """,
        {"signup_token": signup_token, "max_age_minutes": max_age_minutes},
    ).fetchone()
    if row is None or not row["is_valid"]:
        return None
    return row


def create_session(
    conn, token_hash: str, user_id: int, expires_at: datetime, now: Optional[datetime] = None
):
    now = now or datetime.utcnow()
    conn.execute(
        """
        INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_seen_at)
        VALUES (%s, %s, %s, %s, %s)
        """,
        [token_hash, user_id, now, expires_at, now],
    )


def get_session_by_token_hash(conn, token_hash: str) -> Optional[dict]:
    return conn.execute(
        "SELECT * FROM sessions WHERE token_hash = %s", [token_hash]
    ).fetchone()


def touch_session(conn, token_hash: str, now: Optional[datetime] = None):
    now = now or datetime.utcnow()
    conn.execute(
        "UPDATE sessions SET last_seen_at = %s WHERE token_hash = %s",
        [now, token_hash],
    )


def delete_session(conn, token_hash: str):
    conn.execute("DELETE FROM sessions WHERE token_hash = %s", [token_hash])
