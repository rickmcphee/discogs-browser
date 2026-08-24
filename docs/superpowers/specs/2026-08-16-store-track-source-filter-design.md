# Store & Track Source Filter — Design Spec

_2026-08-16_

## Overview

The Store and Track tabs currently have no in-context way to narrow which
crawlers' results a user sees. The closest thing today is [`2026-08-02-store-view-filter-design.md`](2026-08-02-store-view-filter-design.md)'s
"View" toggle, buried in the Settings tab, backed by a `localStorage` set
(`hiddenCrawlerIds`) — opt-out, one crawler at a time, not synced across
devices, and inconvenient to reach while actually browsing.

This spec replaces that mechanism with a "Source" filter button in the
Store/Track header itself, backed by a per-user server-persisted hidden-set,
plus a coarse genre grouping (marketplace/punk/metal/rock/pop) so the
long crawler list can be bulk-toggled instead of checked one at a time. It
also removes the now-empty Settings tab for non-admin users.

## Goals / non-goals

**Goals**
- A "Source" button in the Store and Track tab headers (right side, next to
  the existing list/tiles view toggle) opens a dropdown for narrowing which
  crawlers' results appear.
- The dropdown has two sections: **by genre** (5 coarse buckets, bulk
  toggle) and **by store** (every crawler individually, grouped under its
  genre heading).
- The selection persists server-side per user, surviving app restarts and
  device switches.
- Default state (never touched the filter): everything visible — same
  opt-out shape as today, not opt-in.
- Store and Track share one selection, matching today's single
  `hiddenCrawlerIds` set threaded through both.
- Settings' crawler "View" column is removed; the Settings nav button is
  removed entirely for non-admin users (admin Settings is unaffected apart
  from losing that column).

**Non-goals**
- No migration of existing `localStorage` state — this ships as a clean
  break; everyone starts with all sources visible under the new system.
- No change to what actually gets crawled (`crawlers.enabled`, admin-only,
  unchanged) — this is a display filter only, exactly like the mechanism it
  replaces.
- No DB-backed, admin-editable genre field. Genre is seeded once as plugin
  source and corrected in source going forward (see Data model).
- No per-genre server-side filtering. Genre is a client-side grouping
  convenience over the existing per-crawler-id hidden set; the backend
  stays entirely id-based.

## Data model

**Genre** follows the exact precedent set by
[`2026-08-12-store-genre-summaries-design.md`](2026-08-12-store-genre-summaries-design.md)'s
`genre_summary`: a plugin class attribute, not a DB column.

- Add `genre: str = "marketplace"` as a class attribute on each catalog
  crawler plugin (`backend/crawlers/*.py`), one of
  `marketplace | punk | metal | rock | pop`.
- `db.get_all_crawlers()` reads it the same way it already reads
  `base_url`/`genre_summary` — `getattr(mod.Crawler, "genre", "marketplace")`
  inside the existing try/except, defaulting to `"marketplace"` on import
  failure (matches the "cosmetic, must not fail the listing" comment
  already there for the other two fields).
- The four release-type crawlers (Amazon, eBay, eBay/CCmusic, Discogs
  Marketplace) set no `genre` attribute, same as they set no
  `genre_summary` today, and fall back to the default `"marketplace"`.
- Retagging a crawler's genre means editing its plugin file and
  redeploying — same as correcting a wrong `genre_summary`. No migration,
  no admin edit UI.

**Hidden set** is a new table, mirroring `library_items`/
`stock_item_judgments` exactly (per-user join table, RLS-isolated on
`app.user_id`, no cross-database complication — `users` and `crawlers` are
the same physical schema, different roles):

```sql
CREATE TABLE IF NOT EXISTS user_hidden_crawlers (
    user_id INTEGER NOT NULL REFERENCES users(id),
    crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
    PRIMARY KEY (user_id, crawler_id)
);

ALTER TABLE user_hidden_crawlers ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_hidden_crawlers FORCE ROW LEVEL SECURITY;

CREATE POLICY user_hidden_crawlers_isolation ON user_hidden_crawlers
    USING (user_id = current_setting('app.user_id', true)::int)
    WITH CHECK (user_id = current_setting('app.user_id', true)::int);
```

Granted to `app_user` the same as `library_items`. Absence of a row for a
given crawler = visible (opt-out default, matching Goals).

## Backend API

Two new endpoints in `routers/settings.py`, next to the existing
`GET`/`POST /user-settings` pair, same auth (any authenticated user, no
admin gate) and same full-replace shape as `update_user_settings`:

- `GET /api/user-hidden-crawlers` → `{"hidden_crawler_ids": number[]}`
- `POST /api/user-hidden-crawlers` — body `{"hidden_crawler_ids": number[]}`,
  replaces the user's entire hidden set in one transaction (delete rows not
  in the new set, insert rows newly present). Returns `{"ok": true}`.

`GET /api/crawlers` gains the `genre` field (from `get_all_crawlers()`,
already merged in alongside `base_url`/`genre_summary`) in the `Crawler`
response — no auth-gating change, every caller already receives this list.

`/api/stock` and `/api/stock/artists` are unchanged — both already accept
`hidden_crawler_ids` as a query param (added by the spec this one
replaces); the frontend just sources the initial value from the new
endpoint instead of `localStorage`.

## Frontend

**State.** `App.tsx` already owns `hiddenCrawlerIds: number[]` as lifted
state threaded into both `StockBrowser` instances and the stock API calls.
That doesn't change shape — only its source does: fetched from
`GET /api/user-hidden-crawlers` on mount (once authenticated) instead of
read from `localStorage`. `toggleCrawlerView` (and a new bulk variant for
genre toggles) update local state optimistically and POST the resulting
full set to `POST /api/user-hidden-crawlers`.

**Source button.** Added to `StockBrowser`'s header
(`frontend/src/views/StockBrowser.tsx`), in the same `ml-auto flex
items-center gap-2` group as the existing list/tiles toggle buttons, to
their left. Since both Store and Track render `StockBrowser` with the same
`hiddenCrawlerIds`/`onToggleCrawlerView` props already, the button and its
dropdown are just one more prop pair — no per-scope logic needed.

**Dropdown panel**, opened by the Source button:
- *By genre* — 5 rows (Marketplace, Punk, Metal, Rock, Pop), each a
  tri-state checkbox computed from the crawler list + hidden set (checked =
  every crawler in that genre visible, unchecked = none visible,
  indeterminate = mixed). Clicking bulk-adds or bulk-removes every crawler
  in that genre from the hidden set in one local update, then fires one
  `POST`.
- *By store* — every crawler, grouped under its genre as a subheading,
  each with its own checkbox reflecting simple membership in the hidden
  set. Toggling one fires the same `POST` with the updated full set.
- A "Show all" link at the top clears the hidden set entirely (empties the
  `POST` body) — a plain affordance for undoing an over-aggressive genre
  toggle, not a separately-asked design decision.

## Settings / nav changes

- `Settings.tsx`'s `renderCrawlerTable` drops the "View" column and
  `onToggleCrawlerView`/`hiddenCrawlerIds` props entirely — that control no
  longer lives here for anyone, admin included (admins get the same Source
  button as everyone else on Store/Track).
- Since Settings today renders *only* these two crawler tables for
  non-admins (confirmed: every other column/section is already
  `isAdmin`-gated), removing the View column leaves nothing for a
  non-admin to see. The Settings nav button in `App.tsx` is gated behind
  `showAdminNav` (the same flag already used for other admin-only nav
  items), removing it for regular users entirely.
- Admin's crawler tables are otherwise unchanged (Last run, Crawl
  enable/disable, Refresh columns all stay).

## Genre seed values

Derived from each crawler's existing `genre_summary`. Rule applied: if the
summary names a metal subgenre (metal/doom/black/death/grindcore/extreme) →
**metal**; else if it names punk/hardcore/ska/oi! → **punk**; else if it
names rock (indie rock, garage rock, post-rock, riot grrrl, folk/blues-rock
adjacent) → **rock**; else if it's pop-primary with no rock/punk word →
**pop**; else (broad-catalog stores, or summaries spanning unrelated
genres) → **marketplace**, per your call.

| Site | genre_summary | genre |
|---|---|---|
| Amoeba Music | across nearly every genre | marketplace |
| Angry Young and Poor | punk and hardcore | punk |
| Asbestos Records | ska, punk, and hardcore | punk |
| Asian Man Records | punk/ska label | punk |
| Big Scary Monsters USA | emo, post-hardcore, math rock | punk |
| Century Media | death, black, gothic metal | metal |
| Cleopatra Records | goth, industrial, new wave, classic rock | marketplace |
| Closed Casket Activities | hardcore and metalcore | punk |
| Craft Recordings | jazz, soul, blues, classic rock | marketplace |
| Deathwish Inc | hardcore and heavy underground | punk |
| Epitaph | punk rock | punk |
| Equal Vision | punk, hardcore, emo | punk |
| Fat Possum | blues, garage rock, Southern indie rock | rock |
| Fat Wreck Chords | melodic punk | punk |
| Father/Daughter Records | indie rock and indie pop | rock |
| Fearless Records | pop-punk, emo, alternative rock | punk |
| Flatspot Records | hardcore and metalcore | punk |
| Jackpot Records | broad new-vinyl selection across genres | marketplace |
| Jade Tree Records | emo and indie rock | rock |
| Kill Rock Stars | indie rock and riot grrrl | rock |
| Napalm Records | power, folk, gothic, symphonic metal | metal |
| Newbury Comics | broad new/exclusive vinyl selection | marketplace |
| Nuclear Blast | extreme and heavy metal | metal |
| Numero Group | soul, funk, gospel, outsider music | marketplace |
| Peaceville | doom, death, black metal | metal |
| Pirates Press Records | punk, oi!, rockabilly | punk |
| Polyvinyl Record Co. | indie rock and indie pop | rock |
| Prosthetic Records | extreme and progressive metal | metal |
| Relapse | extreme metal and grindcore | metal |
| Rev HQ | hardcore punk | punk |
| Rise Records | post-hardcore, metalcore, pop-punk | punk |
| Run For Cover | indie emo and pop-punk | punk |
| Saddle Creek | indie rock and folk | rock |
| Season of Mist | extreme metal | metal |
| Secretly Store | indie rock and singer-songwriter | rock |
| The Sound Garden | broad new/used selection across genres | marketplace |
| Sub Pop Mega Mart | grunge-rooted indie rock | rock |
| Temporary Residence Ltd | post-rock, ambient, experimental | rock |
| Triple B Records | hardcore label | punk |
| Turntable Lab | broad new vinyl selection across genres | marketplace |
| 20 Buck Spin | doom, sludge, death metal | metal |
| Amazon, eBay, eBay/CCmusic, Discogs | *(no genre_summary, release-type)* | marketplace |

**Note:** no crawler seeds to **pop** — every "indie pop" mention pairs
with "indie rock," and the rock word wins the tie under the rule above.
The bucket exists for the future (a pop-specific store) and is legitimately
empty today; this isn't a gap to fix, just worth knowing before someone
wonders why "Pop" shows zero stores.

## Testing

- Backend: `GET`/`POST /api/user-hidden-crawlers` round-trip (empty by
  default, full replace, RLS isolation between two users — mirroring
  existing `library_items`/`stock_item_judgments` RLS tests). `genre`
  present and defaulted correctly in `get_all_crawlers()` output, including
  the import-failure fallback path.
- Frontend: Source button renders and opens the dropdown; genre tri-state
  checkbox reflects mixed/all/none membership and bulk-toggles correctly;
  individual store checkbox updates the hidden set and fires the POST;
  `App.tsx` loads the hidden set from the server on mount instead of
  `localStorage`; Settings nav button absent for non-admin, present for
  admin; Settings crawler tables render with no View column for anyone.
