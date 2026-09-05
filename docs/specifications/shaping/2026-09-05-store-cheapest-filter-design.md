# Store tab "Cheapest" filter design

Date: 2026-09-05
Branch: `claude/store-cheapest-filter-x4tdwl`

## Problem

Several stores stock the same record, and the Store tab shows every one of
them as its own row. Under a Cost sort that is three rows for one pressing,
interleaved with everything else; under any other sort it is three rows in a
cluster, and the user reads the prices off to find the one that matters.
There is nothing to ask for "the cheapest place to buy each of these".

Nothing in the data model groups those rows today. `item_key` is a hash of
artist, title *and the store's URL* (`db.compute_item_key`), so two stores'
copies of one record are two unrelated `stock_items` rows with two unrelated
keys. The "comparison rows" `get_stock_items` interleaves are marketplace
listings crawled *for* one store's key, not other stores' copies. So before a
filter can keep the cheapest row per record, something has to say which rows
are the same record — and the stores do not agree on how to write a title.
"Kid A - LP Black", "Kid A (Black)" and "Kid A — Black Vinyl (Ltd)" are one
pressing at three stores; "Kid A (Red)" is a different one.

## Scope

Touches:

- `backend/title_key.py` — new: `title_key(title)`, the fold of a title two
  stores' rows share when they sell the same pressing.
- `backend/db.py` — `stock_items.title_key` column and the index behind the
  filter; `replace_stock_items` and `upsert_stock_item_from_release` write
  the key; `_backfill_title_keys` runs
  from `init_tenant_schema` for rows that predate the column;
  `_stock_filter_sql` gains `cheapest` and the `_cheapest_clause` it appends;
  `get_stock_items` and `get_stock_source_counts` pass it through.
- `backend/routers/stock.py` — `GET /stock` and `GET /stock/stats` gain a
  `cheapest` query param.
- `frontend/src/api/client.ts` — `getStock` and `getStockStats` gain
  `cheapest`.
- `frontend/src/views/StockBrowser.tsx` — a `Cheapest` checkbox beside the
  Store filter dropdown, persisted to `localStorage` under `stockCheapest`.
- `frontend/src/components/StockStats.tsx` — takes and forwards `cheapest`,
  and includes it in the view key that resets the panel.
- Tests: `backend/tests/test_title_key.py`,
  `backend/tests/test_stock_cheapest.py`,
  `frontend/src/test/stockBrowser.test.tsx`,
  `frontend/src/test/stockStats.test.tsx`, `frontend/src/test/client.test.ts`.

Out of scope:

- **Track tab.** Asked for on Store only. Track also reads differently: it is
  "every place this record I follow is in stock", a comparison view, and
  hiding the dearer stores there hides half of what it is for. The backend
  flag is scope-free, so adding the checkbox there later is a frontend
  change only.
- **Deciding the winner by marketplace price.** See "Decisions".
- **Currency conversion.** The app carries no exchange rates; see
  "Decisions".
- **The toolbar redesign.** See "Toolbar real estate" below — a proposal,
  not part of this branch.

## Decisions

- **Fuzzy, not exact — and a token set, not a string.** Exact-title grouping
  would only ever merge two stores that happened to word a title identically,
  which the examples above show is the exception. The wording differences
  are almost entirely *separators and order* ("- LP Black" vs "(Black)" vs
  "— Black Vinyl") plus *words that only say it is a record*. So the key is
  the set of words left after dropping those, sorted: order and punctuation
  cannot matter because they are not in it. The user's own examples,
  "`<title> - LP Black`" and "`<title> (Black)`", key identically; so do the
  bare "`<title>`" and "`<title> - LP`".

- **Variants stay apart.** The colour *is* the variant, so it is a
  significant word: "Kid A (Black)" and "Kid A (Red)" are two rows under the
  filter, which is what the user asked for. A bare "Kid A" does not merge
  with "Kid A (Black)" either — no store said the bare one is black, and
  guessing would be a merge with nothing behind it.

- **Prefer a false split to a false merge.** The two errors are not
  symmetric: a false split shows one row too many, which the user can see
  and ignore; a false merge hides a listing they might have wanted, and
  nothing on screen says it was hidden. So the noise list is short and every
  doubtful word stays in. `deluxe`, `remastered`, `indie`, `exclusive`,
  `signed`, `opaque`, `translucent`, `cd`, `cassette` all survive, though a
  store may use some of them loosely. What goes is the medium when it is
  vinyl (`vinyl`, `lp`, `ep`, `record`, `wax`, disc sizes, weights, disc
  counts), edition filler (`limited`, `ltd`, `edition`, `pressing`,
  `reissue`, `repress`, `version`, `colour`/`color`, `standard`, `new`,
  `sealed`, `import`, `preorder`), packaging (`gatefold`, `sleeve`,
  `jacket`), and `and` (so "Red & Black" and "Red / Black" agree). `cd` is
  *not* noise on purpose: dropping it would let a CD undercut the LP and hide
  the LP, in a vinyl app. The list is one frozenset in `title_key.py` and is
  meant to be tuned from real mismatches as they turn up.

- **Stored, not computed in SQL.** The fold has a word list and a handful of
  regexes, which are testable in Python and unpleasant in SQL. So both
  `stock_items` writers store `title_key` beside the row — `replace_stock_items`
  from the store's title, `upsert_stock_item_from_release` from the
  `listing_title` the marketplace gave the item it matched, falling back to
  the target's title when it gave none — and a boot-time backfill keys the
  rows written before the column existed. The release path keys from the
  matched item's own name because a release crawler matches by artist and
  title, so what it found can be a different pressing than the target, and
  the row has to compete as the pressing it actually is. (Raised by Copilot
  on PR #294: the first draft wrote the key on the catalog path only.)
  All three sites — both writers and the backfill — use one derivation,
  `title_key(listing_title or title, artist)`, so a row keys the same
  whichever path wrote it.

- **The artist is stripped from the front of a name that carries it.** A
  marketplace's own name for an item is usually "Artist - Title [Variant]",
  while a store keeps the artist in its own column and titles the record
  alone. Without the strip the two never key the same and the filter never
  compares them, which is the false split at its most systematic. So
  `title_key` takes the artist and removes it only as a leading segment
  before a separator (`-`, `–`, `—`, `:`, `/`, `|`), in any of its
  article-swapped spellings; a name with no separator after it is the title
  ("Black Sabbath" by Black Sabbath) and is left alone. (Also raised by
  Copilot on PR #294.)

- **Accents fold on Latin letters only.** NFKD decomposes every script, and
  in most of the others a combining mark is a letter rather than
  decoration: Japanese が is か plus a dakuten, an Indic vowel sign is a
  vowel. Stripping every mark folded "Album が" onto "Album か". The fold
  now drops a mark only after an ASCII letter and recomposes the rest, so
  "Björk" still matches "Bjork" and が stays が. Words are matched in any
  script for the same reason, and the tokenizer keeps a combining mark with
  the word it follows rather than treating it as a separator — Python's
  `\w` excludes the mark categories, so a regex tokenizer split a Devanagari
  vowel sign from its consonant and का keyed the same as कि. (Both raised by
  Copilot on PR #294.)

- **The backfill is a sweep, not a one-shot migration.** The deployment is a
  rolling one across two Fly machines, so after a new machine's boot
  backfill has run, an old binary can still be writing unkeyed rows — a
  store snapshot from a sync it was mid-way through, a marketplace match
  from its worker pool — and a boot-only backfill would never revisit them.
  `backfill_title_keys` therefore also runs at the end of every stock sync,
  beside the dead-queue-row sweep that already lives there, so the first
  sync any new machine completes keys whatever the old one left. It is
  normally a no-op, and a partial index on `title_key IS NULL` makes finding
  that out a lookup rather than a scan. `COALESCE(title_key, title)` in the
  clause covers the window in between: an unkeyed row groups on its raw
  title, which can only split, never merge. (Raised by Copilot on PR #294.) The backfill
  matters more than it looks: the grouping treats every NULL as *one* key,
  so unkeyed rows would not sit the filter out, they would all compete as a
  single record. `COALESCE(title_key, title)` in the clause is the second
  guard against the same thing.

- **Grouped on the artist's bare key, the title key and the currency.**
  Artist through `_artist_sort_sql`, the same article-stripped fold every
  other artist comparison in this repo already uses, so "The Beatles" and
  "Beatles, The" compete. Currency because the app has no exchange rates and
  EUR 20 is not comparable to USD 22 — bucketed exactly as `_price_floors`
  buckets price drops, NULL folded to USD as `formatPrice` reads it. A EUR
  store and a USD store for one pressing are therefore two floors and two
  rows, which is honest: the user can see both prices and there is no
  conversion the app could defend.

- **Store rows compete; marketplace comparisons do not decide.** The
  candidates are `stock_items` rows. A comparison row is a marketplace
  listing crawled for one store's key, and it hangs under whichever store
  row wins, so a cheaper Discogs listing is still on screen — it just does
  not rescue a dearer store's row. Letting comparisons decide would need
  the flattened offers shape everywhere (tiles show own rows only, Stats
  counts stock items), for a distinction the user did not ask for.

- **Ties stay; an unpriced row goes only beside a priced one.** Two stores
  at one price are two places to buy it, and hiding one would be a
  tie-break the user cannot see. A row with no price cannot be the cheapest,
  but alone it is still the only row for that record, so it stays until a
  priced sibling appears.

- **Scoped to the view, not the table.** The competitors are the rows the
  other filters already admit. Two consequences follow, and both are the
  behaviour a user would expect: a store hidden through the Source filter
  never wins on a price the user cannot see, and under Saved the cheapest
  *saved* copy stands rather than vanishing because an unsaved store is
  cheaper. It also means `search` scopes the competition, which is
  harmless: a search that matches one store's wording of a title and not
  another's is the one case where the fuzzy key and the search disagree,
  and the cost is one extra row.

- **A window, not a correlated subquery.** `_cheapest_clause` is
  `s.id IN (SELECT id FROM (… MIN(price) OVER (PARTITION BY key) …) WHERE
  price = floor OR floor IS NULL)`: the view is computed once and the floor
  attached to each row, and the outer query is a semi-join. The inner query
  re-aliases `stock_items` as `s` deliberately — every existing condition,
  including the correlated library fragments, is written against `s`, and
  the nearest scope wins, so the same conditions list is reused verbatim
  rather than re-templated for a second alias. It composes with every
  caller of `_stock_filter_sql` for free, which is the whole reason that
  builder exists.

- **Stats take the flag; the sidebar does not need it.** `get_stock_source_counts`
  counts what the list shows, by construction, so it gets `cheapest` — and
  the result is the one genuinely new number here, "how often is each store
  the cheapest". `get_distinct_stock_artists` is unchanged: every record
  keeps at least one row, so no artist can drop out of the sidebar, and a
  flag that cannot change the answer is one more thing to keep in step.

- **A checkbox, where the filters are.** The user asked for a checkbox, and
  it is the right control: `Cheapest` is orthogonal to the filter dropdown
  (Saved + Cheapest is a real question) so it cannot be an option in it,
  and a two-state toggle is what a checkbox is. It sits immediately after
  the dropdown, so the two row-set filters are adjacent, and takes the same
  44 px tap height as the other mobile controls.

## Toolbar real estate

The Store toolbar's control group is now `Source · Stats · <filter> ·
☐ Cheapest · list · tiles`, plus `Artist` and `Sort` on a phone. This
checkbox fits, but the row is at the point where the next control will not,
and the user raised it. The shape that would give the row back, for
whoever takes it up:

- **One `Filter` popover in place of the dropdown**, in the same anchored
  dropdown / `Sheet` shape `Source` and `Stats` already use, holding the
  All/Recommended/Saved/Overlapped radios and the `Cheapest` checkbox — and
  the room for the next two filters. The trigger reads its state so nothing
  is lost from a glance: `All`, `Saved`, `Saved · Cheapest`. This is the
  cheapest version of the change and the one worth doing first.
- **Fold `Source` into it as a section**, since it is also a filter, leaving
  `Filter · Stats · list · tiles`. `Source` lights up when it is narrowing
  the view, and the merged trigger would need to keep that signal.
- Not `Stats`: it is a readout, not a filter, and the source-stats design
  kept it separate for that reason.

Not done here because it rewrites the dropdown the Store *and Track* tabs
share, and every test that reaches for `getByRole('combobox')`, for a
feature that fits without it. It should be its own branch, with a look at
`RecordBrowser`'s toolbar at the same time.

## Known limitations

- **The fold is a heuristic, and its errors are false splits.** Two stores
  that describe one pressing as "Red" and "Translucent Red", or "Blue" and
  "Indie Exclusive Blue", stay two rows. That is the chosen side of the
  asymmetry above; the fix when a pattern recurs is a word in the noise
  list, not a change of approach.
- **A record the stores spell as two different artists does not merge.**
  Same ceiling as every artist-matching path here — see
  `2026-08-22-bare-form-artist-fold-design.md`.
- **Cost.** On 60k synthetic rows the unfiltered cheapest page takes
  roughly 330 ms against 120 ms plain, and 370 ms under a Cost sort, with
  `stock_items_cheapest_idx` in place (440 and 590 ms without it). Two
  passes over the window per request — one for `total`, one for the page —
  are most of it. Acceptable for an on-demand toggle; if it ever is not,
  computing the winners once into a temp table per request is the next
  step.
- **Nothing marks a row as a winner.** The filter narrows the row set; a
  row does not say which dearer rows it beat, or by how much.

## Testing

`test_title_key.py`: the user's examples key identically; variants stay
apart; the bare title stays apart from a colour variant; order and
separators do not matter; each of the deliberately-kept words keeps its row
apart; a CD does not merge with the LP; accents, case, apostrophes, `&` and
disc-count spellings fold; an all-noise title keeps its own spelling, even
one the phrase removal would otherwise empty ("2LP", "180g"); non-Latin
words count as words, so "Album 日本" and "Album 中国" stay apart, and so do
"Album が" and "Album か"; a leading "Artist - " or "ARTIST: " is stripped
when the artist is known, in the article-swapped spellings too, while a
title that is only the artist's name is left alone.

`test_stock_cheapest.py`, on two-to-three stores stocking one record under
different wordings: the lowest-priced store wins; variants stay apart; the
artist article folds; a tie keeps both rows; currencies do not compete and
NULL is USD; an unpriced row hides only beside a priced one; a hidden store
cannot win; the cheapest saved copy stands under Saved; the winner's
marketplace comparisons hang under it while the loser's cheaper comparison
does not rescue it; the Cost sort's flat path carries the flag with
`total == row_total`; source counts sum to the list's total; the sidebar is
unchanged; `replace_stock_items` writes the key; `init_tenant_schema`
backfills a NULL one; a release-crawler row is keyed from the name the site
gave the item (competing as that pressing), from the target's title when it
gave none, and re-keyed on every pass. Plus `GET /stock` and `GET /stock/stats` with
`cheapest=true`, and `hidden_crawler_ids` alongside it.

Frontend: the checkbox renders unchecked on Store and not at all on Track;
ticking it refetches from page 1 with `cheapest: true` on top of the current
filter and never touches the sidebar request; the choice persists and
restores, and a stored choice does not leak into Track; the Stats panel
receives it and refetches when it changes.

Playwright-dependent code is unaffected; nothing here changes crawling.

## Spec drift

Grepped both spec trees for `_stock_filter_sql`, `get_stock_items`,
`get_stock_source_counts`, `getStock`/`getStockStats`, the `stock_items`
column list, and the toolbar's control order. Amended in place:

- [`2026-08-28-store-source-stats-design.md`](2026-08-28-store-source-stats-design.md)
  — lists `_stock_filter_sql`'s parameters by name; `cheapest` added with a
  dated note.
- [`2026-08-08-crawl-target-expansion-design.md`](2026-08-08-crawl-target-expansion-design.md)
  — carries `replace_stock_items`' `INSERT INTO stock_items` column list
  verbatim; noted that `title_key` now rides in it.
- [`docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`](../../superpowers/specs/2026-07-05-in-stock-crawler-design.md)
  — places the filter dropdown "left of the list/tile toggle"; the checkbox
  now sits between them. Its SQLite-era `stock_items` listing already lacks
  `item_key`, `release_id` and `listing_title` and is left as history, as
  the branches that added those left it.

No spec touched carried a crawler/store/source/plugin/test count in the
passages amended.

## Runtime/agent document impact

No `.agents/` directory exists in this repo. This adds one optional boolean
query param to each of two existing authenticated read endpoints, one
nullable column and one index on a global table, and no new endpoint,
trust boundary, or outbound call. `README.md` and `CLAUDE.md` need no
change: none of `CLAUDE.md`'s stated invariants is touched.
