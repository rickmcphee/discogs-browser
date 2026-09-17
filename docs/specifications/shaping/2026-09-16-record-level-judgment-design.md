# Judge a record once, not every listing of it

Date: 2026-09-16
Branch: `claude/wizardly-goldberg-mxvey2`

## Problem

A judgment is billed per `item_key`, and `item_key` is
`sha256(artist|title|url)` (`db.compute_item_key`). The URL in that hash is
what costs money.

Judgments themselves already persist correctly across a scheduled re-crawl.
`replace_stock_items` deletes and re-inserts a crawler's whole snapshot, but
`stock_item_judgments` carries no foreign key on `item_key`, so verdicts
survive; `get_unjudged_stock_items` filters on `j.item_key IS NULL`, so a
re-appearing item is never re-sent; and price is not in the hash, so a
repriced item keys identically. That contract is stated in the
[store-recommended-filter design](../../superpowers/specs/2026-07-06-store-recommended-filter-design.md)
and holds.

What does not hold is the identity the contract is keyed on. Three ways one
record gets billed more than once:

1. **A re-listing at a new URL.** A shop changes a product slug, moves an item
   to a variant page, or appends a tracking parameter. Same record, same
   shop, new digest, billed again. Nothing in the app can tell this apart from
   a genuinely new product.
2. **A second shop stocking the same record.** Two stores, two URLs, two
   digests, two paid judgments on near-identical input for the same user.
3. **A different pressing of the same record.** The taste question is "does
   this look like something I'd like" — a question about the record. The
   answer for a black pressing and a red one is the same answer, paid for
   twice.

The [token-cost design](2026-09-07-recommendation-judgment-token-cost-design.md)
deferred exactly this, noting it "changes the judgment's identity and its
interaction with `_not_owned_clause`, so it needs its own design." This is
that design.

It also carries a fourth, unrelated leak recorded as still open in Amendment 7
of the store-recommended-filter design: `start_judgment_only` lost its
`stock_sync_running` cross-guard in the crawl-queue refactor, so a user
pressing Refresh mid-sync judges a partially-replaced catalog and pays for
items about to be deleted.

## What already works, and must not be "fixed"

- **Judgments outlive their stock rows.** No FK on `item_key`; the
  delete+reinsert in `replace_stock_items` cannot touch them. An item that
  goes out of stock keeps its verdict and is not re-billed if it returns.
  `get_all_stock_judgments` deliberately drives from the judgments table so a
  judgment with no live stock row still exports.
- **`item_key` hashes the legacy `str.title()` casing**, not the corrected
  casing that gets stored, so an upstream casing fix does not orphan
  judgments. Keep it.
- **Price is absent from the hash.** A repriced item is the same item.
- **The export/import format is keyed on `item_key`.** A judgments CSV
  exported before this change must still import after it.
- **Judgment runs are manual and per-user**, not part of the stock sync. A
  scheduled crawl spends nothing on its own.

## Scope

Keep `stock_item_judgments` exactly as it is: primary key `(user_id,
item_key)`, one row per listing. Do not re-key the table.

That is the load-bearing decision here, and it is worth saying why, because
re-keying on the record is the more obvious design. The judgments table is
read by the Recommended filter (`get_stock_items`'s `s.item_key IN (SELECT
item_key FROM stock_item_judgments ...)`), by `get_recommended_stock_items`,
by `get_all_stock_judgments`, by the CSV export and import, and by
`count_matching_stock_items`. Re-keying means touching every one of them, plus
a backfill that cannot run: `get_all_stock_judgments` supports import-only
rows whose `item_key` has no `stock_item_identities` entry at all, so there is
no artist/title from which to derive a record for them.

Instead the record level is applied at the two points where money is actually
spent:

- **Choosing what to bill for.** `get_unjudged_stock_items` returns one
  representative per unjudged *record* rather than one row per unjudged
  listing.
- **Spending the verdict.** A new `propagate_stock_judgments` copies an
  existing verdict onto every unjudged in-stock listing of the same record, so
  the per-listing rows the readers expect all exist without a second API call.

Every read query keeps working untouched, the export format is unchanged, and
there is no migration of existing judgment rows.

## Design

### `record_key`: what "the same record" means for a verdict

`title_key.py` already answers "are these two rows the same pressing" for the
Cheapest filter and `_price_floors`. It folds a store title to the set of
words that say *which* pressing it is, and it deliberately keeps the words
that name a distinct pressing — colours, `deluxe`, `remastered`, `indie`,
`exclusive`, `signed`. Its docstring gives the reason: "A false split shows
the user one row too many; a false merge hides a listing they might have
wanted, and they cannot tell it was hidden."

That asymmetry is real for Cheapest and absent here. Merging two pressings for
judgment hides nothing: `get_recommended_stock_items` and the Recommended
filter both still return every stock row whose record is recommended, because
propagation writes a per-listing row for each. The only consequence of a merge
is that one taste verdict covers both pressings — which is the correct answer,
since the pressing is not what the model is being asked about.

So judgment uses a different key, `record_key(title, artist)`, which drops
that vocabulary. Coarser than `title_key` there, and — as the sections below
arrive at — deliberately *finer* in two places, because a pressing key can be
forgiving where a record key cannot: it keeps word order and repeats, and it
spares the noise words a record can be *named* with.

Not by word list alone, though. Merging is not free here either — it is
merely *differently* priced. The merged record inherits a verdict and, with
it, a reason written about a different album, which the user reads in the
Store row's info popover. A bare list of colour and edition words would fold
"Purple Rain" into "Rain" and "Black Sabbath" into "Sabbath": colours are
album titles too. The asymmetry runs the other way from a filter's, but it
still runs — a false *split* costs one extra judgment, which is what happens
today anyway, while a false *merge* corrupts an answer.

So the words are dropped only where the store has fenced them off: inside a
bracketed aside, or in a trailing segment behind a separator. "Kid A (Red)",
"Kid A - LP Black" and "Kid A (Deluxe Reissue)" all key as "Kid A"; "Purple
Rain" and "The Black Parade" are untouched, having no fence. Stores write a
variant as an aside overwhelmingly often, so the restraint costs almost none
of the saving.

**A bracket is an aside only when it closes with its own kind.** `Kid A (Red]`
is malformed, and reading it as a fence merges it onto `Kid A` on the strength
of a typo — a merge is the expensive mistake here, and nothing in the title
says the store meant a fence rather than mistyping a real character. So the
pattern pairs each opener with its own closer and a mismatched one is left in
the title, splitting that listing off at the cost of one judgment. This is the
same rule the comma, the slash and the pipe are governed by, applied to the
one punctuation class that *is* a fence. (Copilot, round 48.)

A comma is **not** one of those separators, and neither is a slash or a pipe.
All three failed the same test, in the same way. Ordinary titles use them far
more often than storefronts use them as metadata boundaries, and the words
that follow one are frequently exactly the colour and edition vocabulary the
fence rule strips — so "Red, White & Blue" folded onto "Red", "Ready, Set"
onto "Ready", "Black / Gold" onto "Black" and "Red | Blue" onto "Red". Real
records, merged, with a verdict and a reason belonging to a different album.
The slash is the worst of them, because a two-sided or double-album title is
exactly where one shows up. Dropping all three costs the "Kid A, Indie
Exclusive Blue" and "Kid A | Red Vinyl" spellings, which now key as their own
records: one extra judgment each, the cheap mistake. The same reasoning
removed a bare `set` from the variant vocabulary in favour of the phrase "box
set" — `set` is an ordinary title word, "box set" is not.

The typographic dashes stay beside the bare " - ". They read as the aside that
mark makes, and no title uses one the way a title uses a slash.

That phrase lives in a list `record_key` alone consults, not in the one
`title_key` shares. The distinction is the point of having two keys: a box set
and a single LP are two *pressings* and one *record*, so `title_key` must keep
them apart or the Cheapest filter collapses them and hides a listing, while
`record_key` must fold them or the same album is judged twice. Putting the
phrase in the shared list did exactly the former, silently.

That leaves a residual, stated rather than hidden: a trailing dash segment is
still taken as a fence, so a title like "Black - Gold" merges onto "Black".
The dash is the dominant real spelling of a variant and removing it would gut
the feature — where the comma, the slash and the pipe were all weak signals of
a variant and common in ordinary titles, so each one cost little to give up.

`record_key` keeps the words in the order the title wrote them, and keeps a
repeat as a repeat, where `title_key` folds to a sorted set. That difference
is the difference between the two keys' jobs. The set makes `title_key`
forgiving of word order, which is what a *pressing* key wants — one store's
"Kid A Remastered" and another's "Remastered Kid A" are the same thing to the
Cheapest filter, and mis-grouping there shows the wrong price. A *record* key
cannot afford that forgiveness, because the same set makes "Love Hate" and
"Hate Love" one key, and "Love Love" and "Love" one key: distinct albums by
one artist, merged, one inheriting the other's verdict and a reason written
about it. So `record_key` pays the other price instead — two stores wording
one record in different word orders bill it twice, which is the split
direction. (Raised by Copilot on PR #368, round 21.)

The fence rule governs the *variant* words, and for a while it did not govern
the noise list `title_key` shares with it — those came out wherever they
appeared, and some of them are ordinary naming vocabulary. "Record One" and
"Album One" both keyed as "one": two albums by one artist, merged, one handed
the other's verdict and a reason written about the other. The same false merge
as above, reached through the other word list. (Raised by Copilot on PR #368,
round 22.)

The obvious repair — fence the noise list too, exactly as the variant list is
fenced — is the wrong one, and this repo's own store fixtures say why. Folding
those pages down to their product titles and asking which noise words appear
*outside* a bracket or trailing segment separates the list cleanly in two.
`lp` is the common unfenced word, running right through those titles
("Easter Everywhere LP", "Embrace The Black Light LP (Onyx Marble Vinyl)"),
with `vinyl` behind it; fencing those would bill each of those listings apart from
the same record written plainly, which is most of what this design saves.
`record` also appears unfenced there — and every time it is *naming*
the product ("12\" Record Sleeve", "Vinyl Styl Record Cleaning Fluid") rather
than describing it. Sellers write the format words unfenced and the naming
words only inside a fence, so the split is between the words, not between the
positions.

So a short list of noise words — `record`, `records`, `album`, `new`,
`version`, `colour` and its spellings — is spared unfenced and left to the
fence rule, and the rest of the noise list keeps coming out wherever it
appears. The test is whether a record can plausibly be *named* with the word:
`lp`, `gatefold` and `reissue` are vocabulary only a seller writes, while
"record" and "album" are interchangeable names for the thing itself, which is
what makes them uniquely prone to this merge. Behind a fence they read as the
seller again, so "Kid A (New)" still folds onto "Kid A".

Sparing them widened what counts as an all-noise title, and it had to. With
`record` spared, `12" Record Sleeve` came down to the lone token "record" —
non-empty, so the never-empty fallback stood aside — and collided with `7"
Record Sleeve`, the same accessory in another size: a new false merge intro-
duced by the fix for one. A survivor that is itself noise vocabulary has not
identified anything, so the raw spelling still wins.

`record_key` delegates its folding to `title_key` rather than post-filtering
its output, because the punctuation that marks a fence is exactly what a
token set has already discarded. It inherits the never-empty guarantee by
falling back to `title_key(title, artist)` whenever stripping would leave
nothing — a listing titled only `"Black Vinyl"` keeps its own key rather than
joining a bucket of every such row.

**The artist comes off the front before anything is split.** An artist name
can contain the very separators the fence rule splits on — "AC/DC", "Earth,
Wind & Fire", "Emerson, Lake & Palmer" — and splitting first tore those in
half, leaving halves that no longer matched the artist `title_key` was then
asked to strip. One record keyed two ways depending on whether the store wrote
the artist into the name, which is the bug this whole design is about,
reintroduced one layer down. `_split_leading_artist` cuts the prefix off the
raw title first.

It has a third answer besides "found it" and "no artist here": *present but
unlocated*. Folding can change a string's length, so when the raw spellings
disagree ("Björk" against a stored "Bjork") the boundary is not recoverable in
raw offsets. There the title goes on unsplit for `title_key` to strip, costing
one variant not folded away — a record billed twice at worst, which is the
direction this module errs in on purpose.

**It comes off once, and only once.** `title_key` strips a leading artist
segment too, so handing it the artist after `_split_leading_artist` has
already taken one copy off strips a second: `"Genesis - Genesis - Foxtrot"`
came out keyed as `"Genesis - Foxtrot"` is. Reading that repeat as a
duplicated prefix is a guess — nothing in the title distinguishes it from a
name whose first segment happens to be the artist's — and the guess is wrong
in the expensive direction, since a merge hands one record a verdict written
about another. So the artist is passed on only when nothing came off the
front, which also makes the two branches agree: the unlocated-prefix branch
above already strips exactly once. Deleting the argument outright was the
obvious fix and is wrong — an aside in front of the name (`"(Limited Edition)
Genesis - Foxtrot"`) hides it from `_split_leading_artist`, which anchors at
the start of the title, and once the aside is dropped only `title_key` is
left to strip what it uncovered. (Copilot, round 42.)

Its prefix test applies **both** folds — accents and apostrophes off, then the
punctuation fold — before comparing. They lived on separate paths at first,
this one punctuation-only with an accent-only fallback behind it, so a name
spelled differently in both ways at once matched neither: with "Beyoncé &
Jay-Z", a listing titled "Beyonce and Jay Z - Album (Red)" kept its whole
prefix, and because what followed then read as a variant segment the key came
out as the artist's name with no title in it at all. Being looser than SQL
costs nothing here: this key only decides whether a title leads with its own
artist, and the comparison SQL has to agree with is the one between two
*artist columns*.

Its prefix test compares *bare artist keys* — what `_artist_sort_sql`
computes: punctuation folded ("&" to "and", "-" to a space), then a leading
"the " or trailing ", the" dropped. That fold, in that order, because it is
the record group's own artist half. Postgres already counts "Hall & Oates"
and "Hall and Oates", or "The-Beatles" and "Beatles", as one artist; a prefix
test that did not would fail to see the artist in a title spelling it the
other way, leave it in the key, and file that listing away from its own
record. Two paid judgments for one album — the exact failure this design
exists to remove, reappearing in the machinery meant to remove it.

The order matters on its own: folding *after* expanding the article forms
leaves "The-Beatles" with no " the " to find at all, since its article is
hyphen-joined until the punctuation fold turns that hyphen into a space. And
one key on each side rather than a set of accepted spellings, because the
article can sit on either side — a store's "The-Beatles - Abbey Road" or a
stored "Beatles, The" — and only normalising both catches both.

That is also why every separator in the title is a candidate boundary, tested
by folding what precedes it, rather than the artist's own length being used to
slice: the fold changes length, so an offset taken from the artist lands in
the wrong place for precisely the spellings it needs to catch.

The artist half is not folded into `record_key`. Grouping is
`(_artist_sort_sql(artist), record_key)` — the same pair `_cheapest_clause`
groups by, with `record_key` in place of `title_key`. Reusing the proven SQL
artist expression avoids a second Python spelling of the article-stripping
fold that could drift from it.

### Storage

`record_key` becomes a column on `stock_items` alongside `title_key`, and —
this is the part that is easy to get wrong — **also on
`stock_item_identities`**.

Both, because the two tables have opposite lifetimes and the judgment path
needs the durable one. `stock_items` is a snapshot: `replace_stock_items`
deletes and re-inserts a crawler's whole set on every sync. So asking "does
this record already have a verdict?" by joining judgments to live stock rows
answers *no* precisely when a shop has re-slugged a product — the judgment
outlives the stock row, the stock row that carried its record is gone, and the
re-listing gets billed as new. That is the leak this design exists to close,
and a stock-rows join closes every case except it. `stock_item_identities` is
only ever upserted, one row per `item_key` ever seen, and
`get_all_stock_judgments` already leans on it for the same reason. Verdicts
are matched to records through it.

Both write paths compute the key once and put the identical value in both
tables, so each write aligns the pair for the one observation it is writing.
The standing invariant is the weaker, positive one: **the identity holds the
key of at least one of its live stock rows.** It is not that the two always
match. One `item_key` may carry several live `stock_items` rows whose keys
differ — see the collision section below — and the identity can then equal
only one of them, which is a legitimate resting state and not damage to
repair. What is ruled out is a *write* that puts two derivations of the same
title in the two tables.

The backfill has to reconcile them too, and this is easy to get wrong twice.

First, on the release-crawler path the two tables' own titles genuinely
differ, since `stock_items` keys off `listing_title` (the marketplace's name
for what it matched) while the identity stores the catalog target's name. A
backfill reading each table's own columns would put a different key in each,
and since the judgment path matches identity against stock row, a historical
judgment whose two keys disagree simply stops being found — and is billed
again, silently. So the backfill keys the stock rows first and then takes each
identity's key *from* its live stock row rather than re-deriving it: equal by
copying rather than by two derivations agreeing.

Two later refinements narrow that, and both matter. An identity already
holding the key of *any* of its live rows is left alone, because with a
collision there is no better answer and re-pointing it makes the sweep and the
live writers take the identity in turns. And an identity with no live row at
all is only ever *filled*, never re-derived — its key is the fold of the
marketplace's name for what it matched, which its own `title` is not, so
re-folding would overwrite a valid key and reopen the re-listing leak.

Second, it has to look at every identity with a stock row, not only at those
missing a key. An item out of stock when a new machine boots is keyed from the
identity's own name, there being no `listing_title` to read; restock it from a
machine still running the old binary and the stock row arrives unkeyed, to be
folded by the next sweep from a `listing_title` that may read differently. A
sweep selecting on `record_key IS NULL` alone would find that identity already
set and never look again, and the pair would stay unequal for as long as the
row lived — the same silent re-billing, now permanent. The identity pass
therefore selects on *disagreement*: no key, or a key the live stock row does
not share.

Third — and this is where the two versions before it were still wrong — **it
recomputes rather than asking which keys are missing, and its writes are
conditional on the source fields it read.**

Both corrections come from one writer: a machine still running the previous
binary. Its `INSERT` names neither fold key it does not know about, so
`ON CONFLICT DO UPDATE` *preserves* them while `listing_title` and `title`
move to a new name. It does write `title_key`, which it does know.

That breaks a NULL-only sweep in two separate ways. It leaves a row holding a
fold of a title it no longer has, with **nothing NULL anywhere to mark it** —
and the identity beside it keeps the same stale value, so the disagreement
test above sees agreement and leaves the pair alone. Permanently invisible,
and a false *merge*: the listing joins a record it is not and takes that
record's verdict, which is the direction this whole design errs away from.
And when it races the sweep rather than preceding it, it moves `listing_title`
while leaving `record_key` NULL, so a predicate reading the key columns still
matches and the sweep writes folds of the title it read moments ago — over the
newer title, and over the fresh `title_key` that writer had just computed.
Running both passes in one transaction does not help with either: that buys
atomicity, and what is wanted is isolation, which `READ COMMITTED` does not
give.

So the sweep enforces a rule with no gap in it — the stored pair must equal
the fold of the row's own current artist/title/listing_title, which is exactly
what both live writers put there — and writes only where those three fields
still read as its `SELECT` found them. The source fields and not the key
columns, because the writer this exists for is the one that never touches
those. Yielding costs nothing: the next sweep folds the newer title, which is
the better answer. The identity pass fences on its own `artist` and `title` as well as on the key
it read. The key alone is not enough there: an old writer moving those fields
leaves the key exactly as it found it — a NULL stays NULL, and the trigger's
nulling is a no-op on one — so a key-only predicate still matches and writes
the fold of a title the row no longer has, which the keep-what-is-there rule
above would then preserve for good once the identity is orphaned. Those fields
fence the copied-from-the-stock-row case too, where they are not the value's
source; that costs a run's delay and buys one rule instead of two.

One thing the sweep cannot do, however it selects, is repair a stale key
*fast*. Between a sweep returning and the judgment queries that follow it, a
key an old binary left behind is non-NULL, so the billable set compares it —
groups the listing under a record it is not, and can hand it that record's
verdict. Worse, the per-listing judgment that results outlives the repair: the
`item_key` floor below reads that listing as judged for good, reason and all,
written about a different album. So the write that creates the hazard is the
write that clears it: a `BEFORE UPDATE` trigger on each table nulls a fold key
whose source fields moved without it. NULL is the one value every reader
already handles — skipped by the judgment path, `COALESCE`d to the raw title
by the Cheapest filter, re-folded by the next sweep. The sweep's recompute
stays, because the trigger only guards writes made from now on and rows can
already be stale from before it.

With those, the sweep stays a convenience rather than a correctness guard —
for the cases it is the *only* answer to. An unkeyed stock row is skipped by
everything; a keyed stock row whose key is stale, or whose identity disagrees
with it, is not, and the trigger above is what keeps the first of those from
arising at all.

The sweep itself is **best-effort repair, not a transactional guarantee**, and
saying otherwise would misdescribe it. It commits between its two passes on
purpose (see the deadlock note below), so a crash in between, or a writer
landing between them, leaves a keyed stock row beside an identity that has not
caught up. That is a state the *next* sweep selects on and repairs, and it is
the affordable direction: a sibling billed a second time, rather than a
verdict attached to the wrong record. What must never happen is the sweep
committing a pair that is equal and wrong, and the conditional writes are what
prevent that.

Populated by `replace_stock_items` and `upsert_stock_item_from_release`, and
swept by the boot/end-of-sync backfill that today fills `title_key` only. That
backfill is renamed `backfill_stock_keys` — it no longer fills one key, or one
table — and reconciles all three columns across two committed passes, so a
rolling deploy whose old process is still writing rows it cannot key is
repaired by the next sweep exactly as it already is for `title_key`.

It is not cheap, and that is the trade. Folding every row costs about 850 ms
on a 9,000-row catalog against roughly 1,840 ms for the whole-catalog replace
it follows — where the NULL-only version cost about 23 ms. That 23 ms was a
fast answer to a question that missed the case worth asking about. Both
callers run it off the event loop, since it is CPU-bound Python growing with
the catalog. The partial indexes on the NULL keys go with the query that used
them: the stock pass reads every row regardless, and every write was still
paying to maintain them. Both of the `stock_items` ones stay dropped. The one
on `stock_item_identities` came back later, when the sweep stopped reading
that table whole and asked it only for the unkeyed orphans — which gave the
index a reader again, and is what keeps this bounded by the live catalog
rather than by every URL a shop has ever used.

**One `item_key` can hold two record keys, and is billed once regardless.**
`item_key` hashes artist, title and URL, so two crawlers finding one record at
one URL write two `stock_items` rows under one key — which the schema permits
deliberately. `record_key` folds the *listing* title, so those rows disagree
whenever the two sites name what they matched differently, and only one of
them can supply the identity's single key. Two things follow. The sweep does
not try to pick a winner at all: an identity already holding the key of *any*
of its live rows is left alone, and only one matching none is repaired. It
cannot do better, because no column says which write committed last —
`last_seen` is `CURRENT_TIMESTAMP`, the transaction start, so a writer that
opened first and committed last carries the older stamp. Out-guessing that
makes the sweep and the live writers take the identity in turns, and once a
colliding item holds a verdict, a flip lets listings of the *other* record
inherit one written about the first. And the billable set is
deduplicated by `item_key` after grouping by record, because a verdict is
stored per `item_key`: without that step two groups sharing one would buy a
second model call and nothing else, the same artist and title travelling
twice for one row. That second call is what the deduplication prevents. The
`item_key` floor in `_judged_record_sql` then reads the group that was not
billed as judged from that same row.

**A missing key is not compared at all.** Every query that reads `record_key`
also requires it to be present, on both sides, and an unkeyed stock row simply
sits out that run.

This replaced a fallback, and the history is the argument. The first version
read `COALESCE(record_key, title_key, title)` on stock rows against
`COALESCE(record_key, title)` on identities — the two sides bottoming out at
*different* things, so a folded key met a raw title and matched nothing,
re-billing records whose judgments were sitting right there. Making both fall
back to the raw title fixed the case where both keys were missing and left the
case where one was: a keyed identity beside an unkeyed row, which a rolling
deploy produces routinely. Sweeping before the queries narrowed that window
without closing it, because the crawl worker pool writes stock rows
continuously and takes no part in the stock-sync lock, so an old Machine can
insert an unkeyed row after the sweep commits.

Three findings, one root cause: a key that is absent was being made to stand
for something, and whatever it stood for disagreed with the other side.
Requiring both keys ends the class. A row the sweep has not reached is
skipped, which costs it one run's delay and never costs a charge, and the
sweep's job becomes letting it inherit rather than preventing a charge.

`_judged_record_sql` still matches on `i.item_key = s.item_key` as well as on
the record, and that branch needs no key at all. It is the floor: this exact
listing having been judged reads as judged regardless, so nothing about the
keys can put an item already paid for back in front of the model.

**The floor is deliberately not narrowed by the verdict's `record_key`**, and
that is a decision rather than an oversight, because it is what a colliding
key costs. `item_key` hashes artist, title and URL, so two rows sharing one
are *the same product page*, seen by two crawlers that named what they found
differently. At most one of those two names is right about what is on that
page; they are two descriptions of one thing the user can buy once, not two
things. So the floor gives that URL one verdict, and the Store shows it on
both rows — where narrowing it would instead leave the second row unbillable
(the record branch will not re-offer a key that already has a verdict) *and*
blank, which is worse on both counts. A record that genuinely differs lives
at a different URL, mints its own `item_key`, and is billed and judged there
like any other. What is lost is one page's second reading, not a record.
Copilot raised it in round 36; the remedy it asks for — storage and readers
that distinguish colliding record keys — means re-keying
`stock_item_judgments` off `item_key`, which is the remedy already declined
in rounds 26 and 27 and for the same reason: it destroys every stored verdict
and the CSV round-trip that is keyed on `item_key` with it.

#### The verdict carries its own record

`record_key` is a column on `stock_item_judgments` too, and that one is not a
third copy of the same value — it answers a different question. The other two
say what record a *listing* is; this says what record a *verdict* is about.

Nothing else can say it. A verdict is keyed by `item_key`, and an `item_key`
can legitimately carry live rows that fold to two records, so the identity —
which holds one key — names at most one of the two, chosen by whichever
writer ran last. Reading the record off the identity therefore attributes a
verdict about one record to the other exactly when the two disagree, and does
it silently in both directions: the record the verdict was *not* about is
suppressed from the billable set as already judged and never sent, and
propagation then hands it that verdict, with a reason written about a
different album. Round 31's destination-side gate does not reach this, since
it checks the row the verdict is being copied *to*; a genuine listing of the
second record has an identity that agrees with itself and passes.

A NULL means the record was not recorded: a verdict written before the column
existed, or one that arrived by import, which carries no record attribution at
all. An import also *clears* the column on a row it updates, because the
verdict it replaces was about a record the imported one need not be — and
because the fallback below applies only to a NULL, leaving a stale key there
would make an unattributable verdict a confident record-level source and skip
the guard entirely.

A NULL falls back to the identity, and only where the `item_key` is
unambiguous — no live row of it says anything but what the identity holds.
That keeps the two cases this design exists for: a re-listing has no live rows
under its old key, so nothing contradicts the identity, and a record stocked
by two shops has one key each with its own agreeing rows. Only a genuine
collision is excluded, and there it costs a re-billing rather than a crossed
verdict, which is the trade this whole design makes everywhere else too.

An **unkeyed** live row counts as disagreement, so the test is `IS DISTINCT
FROM` rather than an inequality between two present keys. A row with no key
yet has not said the identity is right: it is what the trigger leaves behind
when an old Machine moves a title mid-deploy, and the sweep that follows may
fold it to a different record. Reading that silence as agreement is the same
false merge in slower motion, and waiting costs only a run's delay — the trade
`_unjudged_record_where` already makes for an unkeyed row. Rows that are
*absent* are not silence in that sense, so the re-listing fallback survives.

The column is deliberately **not** backfilled from the identity. Wherever the
fallback is safe the two agree anyway, so a backfill would write nothing new;
wherever they disagree, the identity is precisely the guess the column exists
to stop trusting, and freezing that guess would make it permanent.

### Propagation

```
propagate_stock_judgments(conn, user_id) -> int
```

One `INSERT ... SELECT`. For every in-stock listing that has no judgment for
this user, is not owned, and whose record has a judgment for this user, insert
a copy of that verdict and reason.

The predicate is deliberately the same one `get_unjudged_stock_items` uses —
in stock, unjudged, `_not_owned_clause`. Propagation writes a row exactly
where it prevents a charge and nowhere else, which keeps the judgments table
meaning "verdicts this user would have paid for" and keeps the CSV export from
filling with rows for records the user already owns.

Where a record has more than one verdict among its listings (possible today:
they were billed separately before this change, and a user can import a file
that disagrees with itself), the newest `judged_at` wins, ties broken on
`item_key` so the result is deterministic.

It is called from `_run_judgment_phase`:

- **Ahead of everything**, `backfill_stock_keys` keys whatever the last sync
  left unkeyed, so this run can judge and inherit for those rows. It is not a
  correctness guard — the billable set and propagation both skip an unkeyed
  row rather than comparing it, so such a row is never mis-billed whether the
  sweep ran or not. What it buys is participation in *this* run rather than
  the next. That is also why it needs no atomicity with the queries after it:
  the crawl worker pool writes stock rows continuously and takes no part in
  the stock-sync lock, so an old Machine can add an unkeyed row a moment after
  the sweep commits, and that row simply sits out this run. What it does *not*
  rest on is the sweep being atomic, because it is not: it commits between its
  two passes, so a crash or a writer in between leaves an identity for the
  next sweep. What it rests on is the sweep never committing a pair that is
  equal and wrong, which the conditional writes give it — see the backfill
  section above. Normally a no-op, at the cost recorded there.
- **Before** selecting the unjudged set — but not to keep anything out of it.
  The billable set is grouped by record and anti-joined on the record, so a
  listing whose record already holds a verdict is excluded whether its own
  row has been written or not. What running first buys is the two things only
  a written per-listing row gives: `inherited`, so a run that spends nothing
  still reports having done something, and the rows the Recommended filter
  matches on, in place for the view the user refreshes into.
- **After each batch's upsert**, so a verdict just paid for reaches its
  sibling listings while the run is still going. The Recommended filter
  updates per batch (see the
  [live-recommended-filter design](../../superpowers/specs/2026-08-22-live-recommended-filter-design.md)),
  so deferring the fan-out to the end of the run would show a partial set for
  the duration of it.

The count is logged, carried on the `stock_judgment_complete` event as
`inherited`, **and recorded on the run's own row**, so the saving is visible
rather than merely believed. On the row and not only on the event, because
that event reaches subscribers of the Machine running the job and the
deployment does not guarantee it is the one holding a given browser's stream —
which is the whole reason `stock_judgment_runs` exists (see the
[stop-a-run design](2026-09-16-stop-recommendation-run-design.md)). A client
following by polling would otherwise be told a run that spent nothing checked
nothing. `record_stock_judgment_progress` and `finish_stock_judgment_run` both
carry it, a claim resets it beside `judged`, and `GET /api/stock/judge/status`
returns it on the run.

That counter is not only cosmetic, and the client has to read it. A run that
inherits without judging reports `judged: 0`, and the
[live-recommended-filter design](../../superpowers/specs/2026-08-22-live-recommended-filter-design.md)
flips `hasJudgedItems` on `judged > 0` — a rule written when "this run wrote
judgments" and "this run judged something" were the same statement. They are
not any more, so the completion handler takes either count, and the status
line says how many listings were matched rather than reporting nothing
happened.

### What it costs to run

Measured on a throwaway 9,000-row catalog shaped like the problem — 3,000
records at three listings each, half of them already judged:

| | before | after |
| --- | --- | --- |
| billable items | 7,500 listings | 1,500 records |
| `count_unjudged_stock_items` | — | 74 ms |
| `get_unjudged_stock_items` | — | 79 ms |
| `propagate_stock_judgments` | — | 141 ms, 3,000 rows written |

Two things that reading the SQL would get wrong. The record anti-join *looks*
like a correlated per-row subquery and is not: the planner rewrites it to a
single hash right anti-join, so it is one pass over each table rather than
9,000 lookups.

And an index on `stock_items` over the record pair — the obvious companion to
`stock_items_cheapest_fold_idx` — was written, measured and then removed. It
changed nothing (74/79/141 ms with it, 74/79/141 ms without), because the
planner reads most of `stock_items` either way and hash-joins. Keeping it
would have meant maintaining a three-nested-regexp expression index on the
hottest write path in the app — `replace_stock_items` rewrites a crawler's
whole set every sync — to buy a plan Postgres does not choose.

The index on `stock_item_identities` stays, and the case for it is the
opposite one. Postgres *does* choose it, for propagation's correlated
`LATERAL`, and the margin is not marginal:

| on a 9,000-row catalog | with the index | without |
| --- | --- | --- |
| `propagate_stock_judgments` | 77 ms | 2,168 ms |
| whole-catalog `replace_stock_items` | ~1,840 ms | ~1,700 ms |

It is not free, though, and an earlier draft of this document said it was on
the grounds that the table is append-only. That is wrong: rows are never
deleted, but both writers upsert every identity they see, so a sync rewrites
the lot and the index is maintained along with them. The trade is a read path
28× faster for about 8% on a write path that already runs in seconds, on a
schedule — worth it, but worth stating as a trade rather than a freebie.

The index keys `record_key` bare, matching what the query compares. An
earlier version indexed `COALESCE(record_key, title)` and kept that
definition after the query dropped its fallback, which left the index
unusable for the equality and put propagation back to scanning every identity
for the artist — a benchmark measuring the old query no longer validating the
new one.

### The billable set

`get_unjudged_stock_items` groups by `(_artist_sort_sql(artist),
record_key)` — the same pair the match uses, with no fallback for the reason
given under Storage — and returns one row per group, unkeyed rows having been
excluded by the `WHERE`.
`DISTINCT ON` picks the representative deterministically: lowest `item_key`
within the group, so a run judging the same catalog twice batches it
identically. The representatives are then deduplicated by `item_key`, since
two groups can share the one row that stores their verdict, and
`count_unjudged_stock_items` counts what survives that — the set the model is
actually sent, not the number of groups.

`ORDER BY MIN(last_seen) ASC` is preserved as the ordering across groups —
oldest-seen record first, so a `recommendation_item_limit` that truncates the
set truncates the newest arrivals rather than an arbitrary slice.

The representative's `artist`/`title` are what travel to the model. They are
one listing's wording of the record, which is what the model saw before this
change too — specifically the title the group's key was folded from
(`COALESCE(NULLIF(listing_title, ''), title)`, the same expression the two
live writers and the sweep use), not the catalog target a release crawler was
searching for. On that path the two genuinely differ: the crawler matches by
artist and title, so what it finds can be a different pressing or a different
record, and sending the target's name asks the model about one record and
files the answer under another.

The group's `record_key` travels back with the representative and is stored on
the verdict — see "The verdict carries its own record" under Storage.

### The sync cross-guard

`start_judgment_only` regains the `stock_sync_running` check it lost, matching
the guard the clear and import endpoints already carry. `start_stock_sync`
does **not** regain its `judgment_running` check: dropping that one was
deliberate and follows from the per-user change, since one user's judgment run
must not block a global stock refresh.

But `stock_sync_running` alone is not the guard it looks like. It reads this
process's `_stock_task` and nothing else, while stock sync is serialized
*across Machines* by `STOCK_SYNC_LOCK_KEY` — the whole `on_another_instance`
apparatus in `start_stock_sync` exists because more than one Machine serves
this app. A judgment request routed to the Machine that is not syncing sees an
idle process and starts anyway, which is the leak reopened by routing rather
than closed. So the guard asks the lock as well, via `db.advisory_lock_held`,
reading `pg_locks` rather than probing with `pg_try_advisory_lock` — a probe
would have to take the lock to learn it was free, and the moment it held one a
genuine sync start would report itself as running on another instance.

It fails open. A cost guard that turns a database hiccup into a dead Refresh
button is worse than one that occasionally lets a run through.

The same argument applied to the **per-user** guard, and this branch first
answered it the same way, with a second advisory lock keyed
`(namespace, user_id)`. That is no longer here.
[`2026-09-16-stop-recommendation-run-design.md`](2026-09-16-stop-recommendation-run-design.md)
landed on `main` while this branch was in review and answers it better, with a
`stock_judgment_runs` row a run claims and heartbeats. Both stop two Machines
billing one user twice; the row also survives the case a session lock cannot,
a worker wedged in a blocking call, whose lock no later Refresh can ever
reclaim. So `start_judgment_only` takes main's claim, and the lock this branch
added went out with it — along with its release `finally`, its dedicated
connection, and the asyncio start lock that ordered two requests within one
process.

What is left here is the sync guard, which the claim does not cover: it asks
about *other* work, not about this user's run, and there is no row to read.
It runs **before** the claim, so a refused start leaves no row behind for the
user's next Refresh to be turned away by.

On its own that is check-then-act: a sync can take the lock after the read
returns false and before the claim commits, and `start_stock_sync` has no
mirror guard by design, so both would then run. The claim therefore reads the
lock a *second* time, with its row written and not yet committed, and rolls
back when a sync is found holding it. A rollback rather than a claim-and-close
keeps the no-row property above.

That second read is the **decision point, not a linearization point**, and the
distinction is worth keeping straight: it is not atomic with the commit
either, so a sync can still take the lock in the gap between them. What it
buys is that the decision is made as late as the claim can make it, with the
row already written — the window shrinks from the whole of
`start_judgment_only` to that gap. A sync starting after the decision is the
overlap the missing mirror guard permits on purpose, which is also the reason
the residue is not worth chasing.

Read rather than taken, for the reason `advisory_lock_held` gives: a probe
would hold the lock for a moment, and a genuine sync's own
`pg_try_advisory_lock` would fail against it. And read inside a savepoint, so
the second read can fail open like the first — an error on that connection
would otherwise abort the claim's transaction and leave nothing to commit,
which is the dead Refresh button the fail-open rule below exists to avoid.

That guard fails open, where `start_stock_sync`'s lock does not, and the
asymmetry is deliberate. A stock sync that cannot take its lock *must not
run* — concurrent `replace_stock_items` calls corrupt the shared catalog. A
judgment refused only risks one duplicate charge in the rare case that a sync
is genuinely running elsewhere at that moment, and a dead Refresh button
whenever the database hiccups is the worse trade.

`start_judgment_only` therefore returns `{"started", "stock_sync_running"}`
rather than main's bare bool. Two different refusals reach the caller and it
cannot tell them apart: the router reads the run row for "already running",
but a sync refused this start *without writing one*, and on another Machine
`crawl_manager`'s own flag reads false. Only `start_judgment_only` knows, so
the router forwards that field and derives the rest — `running` and the run
itself — from the row, as main's version does.

The client does need a change, and skipping it would reintroduce a failure
this app has already had once. `handleRefreshRecommendations` discards the
response, so a `started: false` would render as nothing at all — the exact
complaint recorded above `reportStockSyncRejection`, whose comment reads:
"That came back as a started=false nobody rendered, so the click looked like
it had done nothing." A rejected Refresh must say why.

`POST /api/stock/judge/start` therefore gains a `stock_sync_running` flag
alongside `{started, running}`, so the client can tell "your own judgment run
is already going" from "the catalog is mid-refresh". Three layers, each doing
one thing: the guard itself lives in `start_judgment_only` so no other call
site can bypass it and it alone knows which refusal happened; the router
forwards that answer rather than re-deriving it; and
`handleRefreshRecommendations` (`frontend/src/App.tsx`) picks the message the
user actually reads.

## Deferred: the artist half is not accent-folded

The record group is `(_artist_sort_sql(artist), record_key)`, and only the
second half of that pair normalises accents and apostrophes. `_artist_sort_sql`
folds `&` to `and` and strips the article, which is all SQL does here — so two
stores reporting `Beyoncé` and `Beyonce` for one record produce the same
`record_key` and two different artist keys, and stay two billable groups that
cannot inherit each other's verdict. `Guns N' Roses` against `Guns N Roses` is
the same. (Raised by Copilot on PR #368, round 17.)

**This is not fixed here, deliberately.** The cost of leaving it is one extra
judgment for a record whose artist is spelled inconsistently across stores —
a false *split*, the direction this design errs in everywhere else on purpose.
The cost of fixing it is not proportionate to that:

- `_artist_sort_sql` is not the judgment path's private expression. The
  Cheapest filter groups on it, `catalog_artist_bare_fold_idx` and
  `stock_items_artist_bare_fold_idx` are built on it, and the Collection,
  Wantlist and Store artist filters all compare through it. Changing it
  changes every one of those and rebuilds two indexes on the largest tables.
- Scoping a second fold to the judgment path alone — grouping,
  `_judged_record_sql`, propagation and the identities index — avoids that
  blast radius but still needs accent stripping *in SQL*, and Postgres has no
  core function for it. `unaccent` is an extension, and it is **STABLE rather
  than IMMUTABLE**, so it cannot appear in an expression index without an
  IMMUTABLE wrapper that misreports its volatility — which the Postgres
  documentation warns silently corrupts the index if the dictionary ever
  changes. The alternative, a `translate()` over a hand-maintained character
  set, is exactly what `_fold` avoids in Python by decomposing with NFKD and
  stripping combining marks only from ASCII-Latin letters.

So it wants its own change, with its own decision about which of those two
costs to take, and a migration for whichever index it lands on. Worth doing;
not worth folding into this one.

## Considered and rejected

- **Re-keying `stock_item_judgments` on the record.** The cleaner data model,
  rejected on blast radius: every reader, the export format, and a backfill
  that import-only rows cannot supply an artist/title for. Propagation buys
  the same saving with no migration.
- **Folding the artist into `record_key`.** Would make the grouping a single
  column, but requires a Python spelling of `_artist_sort_sql`'s
  article-stripping fold that can drift from the SQL one. The pair is cheap.
- **Using `title_key` unchanged as the judgment key.** One fewer concept, and
  it fixes the re-listing and second-shop cases. It does not fix different
  pressings of one record, which is the case with the most listings behind it.
- **Propagating to owned listings too.** Simpler predicate, but it inflates
  the table and the CSV export with rows for records the user owns and would
  never have been billed for.
- **Running propagation at the end of a stock sync** so the Recommended filter
  updates without a Refresh. The sync is global and `stock_item_judgments` is
  RLS-scoped per user, so it would need an admin-scoped sweep across every
  user. The saving does not depend on it: the next Refresh inherits for free
  either way.
- **Re-judging when the collection changes.** Out of scope here as it has been
  since the original design — a judgment is a snapshot.

## Testing

- `record_key` merges pressing variants that `title_key` splits (black/red,
  deluxe, remastered, half-speed), whether bracketed or behind a trailing
  separator, and still splits genuinely different records (`Greatest Hits` vs
  `Greatest Hits Volume 2`).
- An unfenced colour word is kept: `Purple Rain` does not key as `Rain`, and
  `Black Sabbath` does not key as `Sabbath`.
- A bare number does not carry a fenced segment on its own — `Greatest Hits
  (2)` and `Now - 4` stay separate records — while a digit beside a variant
  word (`Numbered 123`, `2024 Reissue`, `2LP`) still folds away.
- Word order and repeats separate records: `Love Hate`/`Hate Love` and
  `Love Love`/`Love` key apart, while the pressing key still groups them
- A noise word a record can be named with is kept outside a fence and dropped
  inside one: `Record One`/`Album One`, `The Record`/`The Album`, `New
  Order`/`Order` and `Colour By Numbers`/`By Numbers` key apart, while `Kid A
  (New)` still folds onto `Kid A` — and the format words a seller writes still
  fold unfenced, so `Easter Everywhere LP` keys as `Easter Everywhere`. A
  title left holding only noise vocabulary keeps its raw spelling, so `12"
  Record Sleeve` and `7" Record Sleeve` stay apart.
- The sweep's cost tracks the live catalog and not the identities table,
  which nothing prunes
- A comma, a slash and a pipe are not fences: `Red, White & Blue`, `Ready,
  Set`, `Black / Gold` and `Red | Blue` stay separate from their first
  segment, while the dashes and brackets still fold
- A live stock row with no key yet is nothing for the identity pass to copy —
  it leaves the identity alone rather than erasing a key it still holds
  correctly
- A comma is not a fence: `Red, White & Blue` and `Ready, Set` stay separate
  from `Red` and `Ready`, and `Box Set` still folds while a bare `Set` does
  not.
- An unkeyed stock row is skipped by the billable set and by propagation
  rather than compared, and inherits once the sweep has keyed it.
- A title made entirely of variant words keeps a non-empty key.
- Two shops stocking one record yield one entry in the billable set, and
  judging it writes a row for both listings.
- A record re-listed at a new URL after judgment is not in the billable set,
  and inherits a row for the new URL.
- A record judged only for a pressing the user does not own propagates to the
  other pressing.
- Propagation does not touch an owned listing, another user's judgments, or a
  listing that already has one.
- Where a record's listings carry disagreeing verdicts, the newest wins.
- The billable set's representative is stable across repeated calls.
- `backfill_stock_keys` fills a NULL `record_key` on a stock row whose
  `title_key` is already set, and on an identity row.
- It also reconciles an identity whose key its live stock row does not share —
  the pair a boot sweep plus an old binary's restock leaves — and having done
  so, finds nothing left to do on the next call.
- Neither pass overwrites what a worker committed between its own read and
  write: one test races the stock pass, one races the identity pass, and each
  asserts the worker's newer key survives in *both* tables.
- The sweep does not deadlock against a release-crawler write holding the
  identity it is about to want — the test drives exactly that interleaving and
  fails with `DeadlockDetected` if the two passes share a transaction.
- An inherit-only run records what it inherited on its row, and a fresh claim
  resets that counter beside `judged`.
- A stale key with nothing NULL to mark it — the state an old binary leaves by
  moving `listing_title` while preserving `record_key` — is repaired, and the
  same writer racing the sweep gets its newer title left alone rather than
  overwritten.
- That write no longer produces the state at all: the trigger clears the key
  it left behind, leaves the `title_key` it wrote correctly, does not fire for
  the sweep's own writes (which would never converge), and does not fire for a
  live writer that keys what it writes.
- A record whose judged listing is no longer stocked at all still answers
  "already judged" — the case a join through `stock_items` would miss.
- Two simultaneous starts for one user produce one run, and two users'
  simultaneous starts both run.
- Two simultaneous starts for one user produce one run — the sync-lock read
  awaits before the claim, so both requests can reach it and only one may win
  — while two different users' simultaneous starts both run.
- A listing whose `record_key` has not been swept yet still reads as judged
  when it has its own judgment; and with the key missing everywhere, nothing
  is billed at all rather than being compared against something else.
- A judged record's sibling left unkeyed by an old binary — the mixed state,
  not the all-NULL one — sits out the run before a sweep and inherits after
  it, and the judgment run sweeps before it counts.
- A sibling of a record whose identity was left holding a different key is not
  billed again once the sweep has reconciled the two.
- A rejected Refresh does not overwrite a banner something newer has already
  written.
- `start_judgment_only` returns `started: false` while a stock sync runs, and
  starts normally once it finishes.
- It also refuses while another Machine holds `STOCK_SYNC_LOCK_KEY` with no
  local task running — leaving no run row behind — and starts anyway when the
  lock state cannot be read.
- A run cancelled at its opening broadcast closes its claim. That broadcast
  awaits, so it is a cancellation point, and it has to sit inside the `try`
  whose `finally` closes the run's row. This branch had the same hazard
  against the advisory lock the claim replaced, and lost the fix in the merge:
  the mechanism changed, the exposure did not.
- An artist name containing a separator ("AC/DC", "Earth, Wind & Fire") keys
  the same whether or not the store wrote it into the title.
- So does an artist spelled with the punctuation `_artist_punct_fold_sql`
  folds: "Hall & Oates" against "Hall and Oates", "Blink-182" against
  "Blink 182", in either direction — and with the article on either side,
  "The-Beatles" against "Beatles" and "Beatles, The" against "The Beatles".
  An article that belongs to the *title* ("The Wall") is left alone.
- The backfill gives an identity and its stock row the same `record_key` on
  the release-crawler path, and a judgment made before it still reads as
  judged afterwards.
- The Refresh Recommendations click renders a reason when the start is
  rejected, and distinguishes a running sync from a running judgment.
- A completion carrying only `inherited` enables `Recommended` in a client
  whose bootstrap `any_judged` was false, and a completion with both counts
  zero still leaves it disabled.
