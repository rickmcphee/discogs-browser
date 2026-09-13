# Artist punctuation fold design

Date: 2026-09-13

## Problem

The artist sidebar already collapses the spellings of one band that differ by
case (`canonical_artist_labels`) or by article — "The Beatles" / "Beatles, The"
/ bare "Beatles" (`2026-08-16-the-suffix-artist-display-design.md`,
`2026-08-22-bare-form-artist-fold-design.md`). Two more disagreements survive
all of that, and they are as common as either:

- **Hyphen against space.** Discogs has "Blink-182"; a store has "Blink 182".
- **Ampersand against the word.** "Hall & Oates" against "Hall and Oates",
  "Earth, Wind & Fire" against "Earth, Wind and Fire".

Neither carries any article marker, so no existing fold touches it. Each
spelling gets its own sidebar entry holding a slice of the records, and
clicking either shows only that slice — the same defect the article folds
exist to fix, in a different disguise. Nothing about the split is visible as a
split: two adjacent entries with the same name, one of them missing records
the user knows they own, reads as a data problem with the library.

## Approach

One fold, `_artist_punct_fold_sql` (`backend/db.py`), normalizing both
equivalences at once, plus `_artist_punct_fold` as its Python twin for the two
Python-side key functions (`_artist_sort_key`, `_is_bare_artist_input`):

```
BTRIM(regexp_replace(REPLACE(REPLACE(col, '&', ' and '), '-', ' '), '\s+', ' ', 'g'))
```

"&" becomes " and " rather than "and" becoming "&" because that direction is a
plain character replacement: it needs no word boundary, so it folds an
unspaced "Hall&Oates" onto the spaced spelling as readily as the spaced one,
where an and-to-ampersand rule would first have to prove "and" is a whole word
("Sandwich" must not become "S&wich"). The spaces that replacement introduces
are then collapsed and trimmed, and that same collapse is what makes "-" → " "
hold for a name written "Blink - 182".

**Keys only, never labels.** The fold goes into the two artist *key*
expressions and nowhere else:

- the grouping key `canonical_artist_labels` votes on —
  `LOWER(_the_comma_form_sql(_artist_punct_fold_sql(artist)))`, in both its
  main query and its bare-form lookup phase (whose `<bare>, the` join key is
  built from the folded name on both sides);
- `_artist_sort_sql`, which folds internally now, so every one of its callers
  inherits this: the `artist=` equality filters in
  `get_library_releases`/`get_stock_items`, the artist `ORDER BY`,
  `_collection_artist_clause`'s overlapped-artist match, and
  `_cheapest_clause`'s partition key.

The *label* stays whichever raw spelling won the vote, formatted exactly as
before — so the sidebar reads "Blink-182" or "Blink 182", a spelling some
source actually wrote, never a normalized form nobody chose.
`canonical_artist_labels`'s winner-label expression is the one
`_the_comma_form_sql` call site that keeps the raw column.

**Fold first, strip the article second.** `_artist_sort_sql` applies the
punctuation fold to its column *before* its `LIKE 'the %'` / `LIKE '%, the'`
guards, and the grouping key wraps `_the_comma_form_sql` around the folded
column rather than the other way round. Order matters: "The-Beatles" only
reaches either guard as "The Beatles" once the hyphen is a space, and folding
after the strip would leave the two orderings keying that name differently.
The Python `_is_bare_artist_input` folds first for the same reason, so the
question "is this input bare?" gets the same answer on both sides.

**Filter parity** is not a separate mechanism here — it falls out of
`_artist_sort_sql` folding internally, which is the whole of the equality
filter on both sides of the comparison. A click on the merged entry returns
every spelling's rows, and they arrive under the single label the sidebar
showed, because `_apply_canonical_artists` relabels each row through the same
vote.

**Indexes**, renamed rather than redefined:

| Was | Now |
| --- | --- |
| `catalog_artist_the_lower_idx` | `catalog_artist_the_fold_idx` |
| `stock_items_artist_the_lower_idx` | `stock_items_artist_the_fold_idx` |
| `catalog_artist_bare_lower_idx` | `catalog_artist_bare_fold_idx` |
| `stock_items_artist_bare_lower_idx` | `stock_items_artist_bare_fold_idx` |
| `stock_items_cheapest_idx` | `stock_items_cheapest_fold_idx` |

The rename *is* the migration, for the reason `crawl_queue_claimable_idx`
already records in the same schema string: `CREATE INDEX IF NOT EXISTS` under
an unchanged name is a no-op against a database that already holds the old
definition, and the planner matches an expression index by exact AST — so a
deployment that has booted before would keep indexes the new queries can never
use, and every artist-filtered or artist-sorted page would silently fall back
to a sequential scan of `catalog` or `stock_items`. `DROP INDEX IF EXISTS` on
each old name sits beside the new `CREATE`; dropping and recreating under the
unchanged name would instead rebuild all of them on every boot.
`test_superseded_artist_indexes_are_dropped` and
`test_artist_expression_indexes_carry_the_punctuation_fold`
(`backend/tests/test_global_schema.py`) pin both halves. The plain
`LOWER(artist)` indexes are untouched — they serve `_library_match_fragment`,
which takes neither this fold nor the article one (see "Out of scope").

The first boot after this deploys therefore drops and rebuilds those indexes
inside one transaction — the whole `GLOBAL_SCHEMA` script runs as a single
`conn.execute`, so `CONCURRENTLY`, which cannot run in a transaction block, is
not available to it. The blocking level comes from the `DROP INDEX`es rather
than the builds: each takes `ACCESS EXCLUSIVE` on its table (a plain
`CREATE INDEX` takes only `SHARE`, which blocks writes but not reads), and the
single transaction holds every one of them until it commits — so `catalog` and
`stock_items` are locked against reads as well as writes for the whole rebuild,
not just against writers. A one-off stall at startup proportional to those two
tables; every index this schema has ever added paid the `SHARE` half of it
already.

`_artist_punct_fold_sql` needs no `escape_percent` twin of the parameter
`_the_comma_form_sql` and `_artist_sort_sql` carry: it contains no `%` at all,
so one text serves both the parameterized queries and the unparameterized
`GLOBAL_SCHEMA` DDL.

Write-side cost is real and unmeasured: `replace_stock_items` deletes and
reinserts every row for a crawler on each stock sync, and each insert now
evaluates a three-deep `regexp_replace`/`REPLACE` chain per artist expression
index instead of reading the column. Judged worth paying for the same reason
the prior design judged the index count worth paying: the alternative is a
sequential scan on every artist-filtered listing page.

**Frontend**: `reconcileSelectedArtist`
(`frontend/src/views/artistSelection.ts`) applies the same fold to both sides
before comparing. Without it, a label flipping from "Hall & Oates" to "Hall and
Oates" — which the frequency vote can do on any sync — would look like the
artist leaving the list, and the sidebar selection would reset to All. The
equal-length guard that protects against a JS/Postgres case-folding
disagreement (see that file's comments) now applies to the punctuation-folded
strings rather than the raw ones, which keeps it doing exactly its old job:
folding punctuation changes neither "İsis" nor "i̇sis", so that case still
clears, while "Hall & Oates"/"Hall and Oates" — unequal length raw, equal
folded — is now followed.

## Out of scope

- **Search.** `search=` still matches the raw column and the comma-folded
  form, so typing "Blink 182" does not find a row stored "Blink-182". This is
  the same boundary `2026-08-22-bare-form-artist-fold-design.md` drew, for the
  reason it gives: folding both sides of a substring match changes the meaning
  of every artist search rather than just this case, and belongs to its own
  change. Worth noting the asymmetry it leaves is now slightly wider — the
  sidebar can display a spelling that finds fewer rows in the search box than
  it filters to in the sidebar.
- **`_library_release_match_sql`'s owned-release match**, and with it the
  Store tab's Collection/Wantlist filter and the `library_stock_item_keys`
  view the crawl gate reads. It compares `LOWER(artist)` with no fold of any
  kind, so a catalog row "Blink-182" and a stock row "Blink 182" for one
  record are still not recognized as the same release. Same carve-out both
  prior folds made; widening it reaches the crawl queue's gate, not just a
  display filter.
- **`title_key`'s artist-prefix strip.** `_artist_forms` (`backend/title_key.py`)
  tries the stored spelling and its article variants when removing a leading
  "Artist - " from a marketplace's item name; it does not try a
  punctuation-folded one, so a catalog "Blink-182" still fails to strip
  "Blink 182 - " from a listing title, and that row keys apart from a store's
  bare "Dude Ranch". The key itself is unaffected by this class of spelling
  (it tokenises on alphanumerics and treats "and" as noise, so "&" and "-"
  never reach it) — only the prefix strip is. Left alone because `title_key`
  is computed at write time and stored on `stock_items`, so changing its
  derivation is a re-derivation of every row, not a read-path change like
  this one.
- **Disambiguation.** Like the bare-form fold, this assumes a match after
  folding means the same artist. Two genuinely different bands whose names
  differ only by a hyphen or an ampersand would be merged, with no mechanism
  proposed to tell that from the case this exists for.
- **Other punctuation.** En dashes, em dashes, periods ("clipping." against
  "clipping"), "+" against "and", apostrophes, and accents are all untouched.
  Each is a real spelling disagreement; none is as common as these two, and
  each widens the merge risk above. Adding one later is adding a replacement
  to `_artist_punct_fold_sql`/`_artist_punct_fold` and renaming the indexes
  again.
- **A label flip is now possible across spellings, not just casings.** The
  frequency vote can move the displayed label from "Blink-182" to "Blink 182"
  when a store's rows are replaced mid-sync, exactly as it already moves it
  between casings and between bare and comma forms. The frontend follows the
  flip rather than resetting the filter (above), but the label a user sees can
  change under them.
