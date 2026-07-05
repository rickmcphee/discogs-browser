# Wishlist Removal Cleanup — Design Spec

_2026-07-05_

---

## Overview

[2026-07-04-wishlist-design.md](2026-07-04-wishlist-design.md) added wishlist
sync with a soft-clear: removing an item from the Discogs wantlist clears its
`in_wishlist` flag locally, but the release row (and any listings) stays in the
database, invisible to both panes.

This spec changes that outcome for wishlist-only releases: once a release is
no longer in the wantlist **and** was never in the collection, its row and
listings are deleted from the local database, not just hidden. Collection
removal is explicitly out of scope — `in_collection` still never clears once
set, unchanged from today.

## Goals / non-goals

**Goals**
- A release removed from the Discogs wantlist, and not present in the
  collection, is hard-deleted (row + listings) on the next sync.
- Self-heal any wishlist-only releases already sitting orphaned in the
  database from the soft-clear behavior shipped in the prior wishlist spec.

**Non-goals**
- Any change to collection-removal handling (`in_collection` stays sticky).
- A tombstone/grace-period/undo mechanism for accidental deletions.
- Guarding against a truncated-but-successful Discogs API response (see Error
  handling).

## Data model

No schema change. `releases.in_collection` / `in_wishlist` are unchanged. What
changes is what happens to a row once both are `0`: previously left in place,
now deleted.

## Sync flow

`db.py` gains:

```python
def delete_orphaned_releases(conn: sqlite3.Connection) -> list[str]:
    """Delete releases (and their listings) with both in_collection and
    in_wishlist false. Returns the deleted discogs_ids."""
```

It selects `discogs_id` from `releases` where `in_collection = 0 AND
in_wishlist = 0`, calls the existing `delete_listings_for_release` for each
before deleting the `releases` row (no `ON DELETE CASCADE` on `listings.
release_id`), and commits once.

`crawl_manager._sync_collection` calls it immediately after the existing
`clear_wishlist_flags_not_in(conn, wishlist_seen)` call, in the same
try/finally block, using the same connection:

```python
cleared = clear_wishlist_flags_not_in(conn, wishlist_seen)
deleted = delete_orphaned_releases(conn)
log.info("Wishlist sync complete: %d items, %d stale entries cleared, %d releases deleted",
          wishlist_count, cleared, len(deleted))
```

No new SSE event. Deletion rides along with the existing `sync_complete`
broadcast — same visibility characteristics the soft-clear already had (the
frontend doesn't currently auto-refresh `RecordBrowser` on `sync_complete`;
out of scope here since it predates this change).

Because this only fires for rows with `in_collection = 0`, and nothing today
ever sets `in_collection` back to `0` except first-insert of a wishlist-only
release, this only triggers for the "was wishlist-only, now removed from
wishlist" case — exactly the scope requested.

## Error handling

No new guardrails, by explicit decision.

- A failure partway through `iter_wantlist_pages` (network error, bad token)
  raises before `clear_wishlist_flags_not_in`/`delete_orphaned_releases` run —
  the outer `except Exception` in `_sync_collection` catches it, reports
  `sync_error`, and neither function executes. Partial pagination cannot
  cause an incorrect deletion.
- A *successful but truncated* wantlist response (Discogs API returns fewer
  items than actually on the list, without raising) would cause real
  wishlist-only items to be deleted, listings included. This risk already
  existed for the soft-clear; hard-delete makes it more costly to reverse.
  Accepted as-is per explicit decision during design — no page-count sanity
  check is added.
- A release deleted mid-sync while a concurrent price-crawl is writing a
  listing for it raises an FK error on `upsert_listing`; already caught
  per-item by the existing broad `except Exception` in `crawl_releases`
  (`crawler.py`), surfacing as a per-item error event rather than crashing
  the crawl. No new handling added.

## Testing

- `test_db.py`: `delete_orphaned_releases` deletes a release with both flags
  `0` and its listings; leaves alone a release with `in_wishlist=1` and one
  with `in_collection=1`.
- Update/extend the existing wishlist sync log-line assertion, if any, for the
  new deleted-count field.
