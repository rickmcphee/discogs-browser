# Collection "No Plex" Filter — Design

**Date:** 2026-07-09

---

## Overview

[2026-07-08-plex-integration-design.md](2026-07-08-plex-integration-design.md) added a `plex_url` match to each collection release, surfaced as a title hyperlink. This spec adds a way to isolate the releases that *don't* have one — a quick way to see what's left to rip.

It mirrors the Store tab's existing filter dropdown ([2026-07-06-store-recommended-filter-design.md](2026-07-06-store-recommended-filter-design.md)) exactly: a `<select>` with `All`/`No Plex` options, server-side filtering, and the second option disabled until its dependency (here, Plex being configured, there, an Anthropic key) is present.

## Goals / non-goals

**Goals**
- A `No Plex` filter option on the Collection tab, narrowing the table (and the artist sidebar) to releases with no `plex_url`.
- Disabled in the dropdown when Plex isn't configured (`plex_base_url`/`plex_token` both set), mirroring `Recommended`'s `!recommendedAvailable` gating on the Store tab.
- If Plex becomes unconfigured while `No Plex` is selected, fall back to `All` — same guard Store already has for `Recommended`.

**Non-goals**
- Any dropdown/filter on the Wishlist tab. Plex matching only ever runs against `in_collection = 1` releases (see the linked spec's `get_releases_for_plex_match`), so every wishlist release already has `plex_url = NULL` — a "No Plex" filter there would always select 100% of items. Decided during brainstorming: Collection tab only.
- A "Matched" / "Has Plex" counterpart option. Not asked for; `All` already shows everything, and YAGNI applies to a third dropdown option nobody requested.
- Any change to how matching itself works, or to the title hyperlink.

## Backend

`db.get_releases` (`backend/db.py:216`) and `db.get_distinct_artists` (`backend/db.py:534`) each gain a `no_plex: bool = False` parameter, adding `r.plex_url IS NULL` (or `plex_url IS NULL` for the artist-list query, which selects directly from `releases`) to their `conditions` list when set — the identical shape `overlapping`/`recommended` already use in `get_stock_items`/`get_distinct_stock_artists`. No new SQL join, no new table.

`GET /api/releases` and `GET /api/artists` (`backend/routers/releases.py`) each gain `no_plex: bool = Query(False)`, passed straight through to the corresponding `db` function. The backend doesn't need to guard "no_plex only makes sense with scope=collection" — same precedent as the Store filters, which don't enforce mutual exclusion server-side either; the frontend simply never sends `no_plex=true` outside the Collection tab, because the dropdown that sets it doesn't render anywhere else.

## Frontend

`RecordBrowser.tsx`:
- New state: `filter: 'all' | 'no_plex'`, initialized from a new `localStorage` key `collectionFilter` (mirrors Store's `stockFilter`).
- New prop: `plexAvailable?: boolean`.
- A guard effect mirroring Store's: if `!plexAvailable && filter === 'no_plex'`, reset to `'all'`.
- The `<select>` renders **only when `scope === 'collection'`** — placed in the toolbar next to the view-mode toggle buttons, same position/styling as Store's dropdown:
  ```tsx
  <option value="all">All</option>
  <option value="no_plex" disabled={!plexAvailable}>No Plex</option>
  ```
- `load()` passes `no_plex: filter === 'no_plex'` to `getReleases`; the artist-list effect passes the same to `getArtists`.
- Selecting a filter resets `page` to 1 (same as changing `selectedArtist`/`search` elsewhere in this file).

`api/client.ts`:
- `getReleases`'s params object gains `no_plex?: boolean`.
- `getArtists(scope?: RecordScope, noPlex?: boolean)` — a second positional parameter, matching `getStockArtists(overlapping?, recommended?)`'s existing style rather than turning a single-arg function into an object-arg one.

`App.tsx`:
- The existing settings-fetch effect (`App.tsx:45`, which already derives `hasAnthropicKey` from `getSettings()`) gains one more derived boolean: `hasPlexConfigured = Boolean(s.plex_base_url && s.plex_token)`.
- Passed as `plexAvailable={hasPlexConfigured}` to both `<RecordBrowser>` instances (harmless no-op on the Wishlist one, since its dropdown never renders — same reasoning already applied to passing `syncing` to both).

## Testing

- Backend: `get_releases(no_plex=True)` returns only releases with `plex_url IS NULL`; `get_distinct_artists(no_plex=True)` narrows the same way. Combined with `scope="collection"` (both conditions ANDed) and with `search`/`artist` already-existing filters, to confirm no interaction bugs.
- Backend: `GET /api/releases?no_plex=true` and `GET /api/artists?no_plex=true` round-trip through the router.
- Frontend: dropdown renders on Collection, not on Wishlist. `No Plex` option is `disabled` when `plexAvailable` is `false`/absent. Selecting `No Plex` calls `getReleases`/`getArtists` with `no_plex: true`. Filter resets to `all` when `plexAvailable` flips to `false` while `no_plex` is selected.

## Out of scope

- Wishlist filter (see Non-goals).
- A "Matched" option (see Non-goals).
- Persisting the filter differently per scope — moot, since it's Collection-only.

## Success criteria

- On the Collection tab, selecting `No Plex` shows only releases without a matched Plex album, and the artist sidebar narrows to match.
- With Plex unconfigured, `No Plex` is visibly disabled in the dropdown, exactly like `Recommended` is when no Anthropic key is set.
- Switching to the Wishlist tab shows no filter dropdown, unchanged from today.
- Selecting `All` after `No Plex` returns to the unfiltered collection.
