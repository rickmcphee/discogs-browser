# Store genre summaries (hover tooltip)

## Problem

The Store Management section (Settings tab) lists ~36 catalog-crawler stores by name only. A user deciding whether to hide a store (the existing View toggle) has no way to tell what kind of music it carries without following the link out to the site.

## Scope

Applies only to `catalog`/`catalog_browser` crawlers (the Store Management / Stores table — "Stores" was "Store Catalog Sources" as of a later, separately-documented rename). The four `release`-type crawlers (Amazon, eBay, eBay/CCmusic, Discogs Marketplace) are general marketplaces with no coherent genre focus and get no summary — their tooltip stays absent, same as today.

## Design

**Storage:** follow the existing `base_url` pattern exactly — a class attribute on each crawler plugin (`backend/crawlers/*.py`), not a DB column. [`db.get_all_crawlers()`](../../../backend/db.py) already dynamically imports each plugin module to read `base_url`, inside a try/except that logs and falls back to `None` on import failure. Add `genre_summary` to that same read, same fallback. No migration, no new endpoint, no admin edit UI — an admin who wants to change a summary edits the plugin file, same as they would `base_url` or `site_name` today.

**API:** add `genre_summary: string | null` to the `Crawler` TS interface, alongside `base_url`.

**UI:** in `Settings.tsx`'s `renderCrawlerTable`, add `title={c.genre_summary ?? undefined}` to the store `<a>` (and the plain-text fallback span, for consistency, though every catalog crawler currently has a `base_url`). This is the codebase's existing hover-tooltip convention — native `title` attribute, used elsewhere in this same file (the refresh button) and in RecordBrowser/StockBrowser. No custom tooltip component. Renders identically for admin and non-admin, since both see this table and both can hide stores.

**Amendment (2026-08-17, branch `claude/store-crawler-filter-design-d16b80`):** the last sentence above no longer holds. Non-admins no longer see the Settings crawler table at all — the Settings nav item is admin-only again, and the table's "View" column (the "both can hide stores" part) is gone. Hiding stores is now done via the per-tab "Source" filter on Store/Track, which does not use this tooltip. `renderCrawlerTable`'s `title={c.genre_summary ?? undefined}` now only ever renders for an admin viewer. See [`2026-08-16-store-track-source-filter-design.md`](2026-08-16-store-track-source-filter-design.md).

**Testing:** extend `test_crawler_crud.py`'s `get_all_crawlers` test with a `genre_summary` assertion. Add `genre_summary` to the mock crawlers in `settings.test.tsx` and assert the `title` attribute renders.

## Content

One sentence per store, written from general knowledge of each label/shop. Confidence is high for well-known labels; a handful of smaller/regional shops are flagged **(verify)** below — please correct any that are wrong before implementation.

| Site | Summary |
|---|---|
| Amoeba Music | Large independent record store selling new and used vinyl and CDs across nearly every genre. |
| Angry Young and Poor **(verify)** | Independent record store and distro focused on punk and hardcore. |
| Big Scary Monsters USA | Label specializing in emo, post-hardcore, and math rock. |
| Century Media | Metal label spanning death, black, and gothic metal. |
| Cleopatra Records **(verify)** | Reissue label spanning goth, industrial, new wave, and classic rock. |
| Closed Casket Activities | Hardcore and metalcore label. |
| Craft Recordings | Reissue label for jazz, soul, blues, and classic rock catalogs. |
| Deathwish Inc | Hardcore and heavy underground label. |
| Epitaph | Punk rock label. |
| Equal Vision | Punk, hardcore, and emo label. |
| Father/Daughter Records | Indie rock and indie pop label. |
| Fat Possum | Blues, garage rock, and Southern indie rock label. |
| Fat Wreck Chords | Melodic punk label. |
| Fearless Records | Pop-punk, emo, and alternative rock label. |
| Flatspot Records **(verify)** | Hardcore and metalcore label/store rooted in skate culture. |
| Jade Tree Records | Emo and indie rock label. |
| Kill Rock Stars | Indie rock and riot grrrl label. |
| Napalm Records | Power, folk, gothic, and symphonic metal label. |
| Nuclear Blast | Major extreme and heavy metal label. |
| Numero Group | Reissue label for obscure soul, funk, gospel, and outsider music. |
| Peaceville | Doom, death, and black metal label. |
| Pirates Press Records | Punk, oi!, and rockabilly label and pressing plant. |
| Polyvinyl Record Co. | Indie rock and indie pop label. |
| Prosthetic Records | Extreme and progressive metal label. |
| Relapse | Extreme metal and grindcore label. |
| Rev HQ **(verify)** | Hardcore punk label/store (Revelation Records). |
| Rise Records | Post-hardcore, metalcore, and pop-punk label. |
| Run For Cover | Indie emo and pop-punk label. |
| Saddle Creek | Indie rock and folk label. |
| Season of Mist | Extreme metal label. |
| Secretly Store | Indie rock and singer-songwriter label group (Secretly Canadian / Jagjaguwar / Dead Oceans). |
| The Sound Garden **(verify)** | Independent record store with a broad new and used selection across genres. |
| Sub Pop Mega Mart | Grunge-rooted indie rock label store. |
| Temporary Residence Ltd | Post-rock, ambient, and experimental label. |
| Triple B Records | Hardcore label. |
| 20 Buck Spin | Doom, sludge, and death metal label. |

## Out of scope

- No summaries for Amazon / eBay / eBay/CCmusic / Discogs Marketplace.
- No admin UI for editing summaries after the fact — same as `base_url`, it's plugin-file content.
- No LLM-generated or dynamically refreshed summaries.
