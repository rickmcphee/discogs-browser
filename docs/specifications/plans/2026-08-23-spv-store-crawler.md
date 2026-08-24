# SPV Entertainment store crawler — task log

Date: 2026-08-23
Branch: `claude/spv-store-crawler-2mdf0z`
Design: [`../shaping/2026-08-23-spv-store-crawler-design.md`](../shaping/2026-08-23-spv-store-crawler-design.md)

**This is a retrospective log, not a forward plan**, and is written as one
deliberately. The sibling crawler plans in this directory were authored before
implementation and drove it; this crawler was implemented first and its plan
noted as missing during review on PR #165. Back-dating a document to look like
it guided work it did not guide would misrepresent the record, so this instead
does the job CLAUDE.md actually assigns to plans — "historical per-feature task
logs" — and records what happened, including the parts that went wrong.

## What shipped

1. `backend/crawlers/spv.py` — `crawler_type="catalog"` plugin over the store's
   `vinyl` collection via `shopify_catalog.iter_products()`. EUR prices;
   quoted-album title parser reusing `asianmanrecords.py`'s two-stage shape;
   negative format gate at product, blurb and variant level; multi-variant
   qualifier.
2. `backend/tests/test_spv_crawler.py` — a `respx`-mocked suite. No case
   count here: it went stale on nearly every commit and was removed from the
   design doc for the same reason. `grep -c '^def test_\|^async def test_'` answers it — the
   plain `^def test_` form misses the three async cases.
3. `frontend/src/views/formatPrice.ts` + test — the price cell rendered every
   currency with `$`. Split out of the crawler work only in the sense that it
   was found by it; it fixes rows that were already wrong.
4. Spec amendments: the shared title-split helper doc (documented exception), the
   in-stock crawler design, and the store-recommended-filter design.

## The part worth recording

**The crawler was never run against the live store.** The session's egress
proxy answers 403 to `CONNECT store.spv.de:443`, and to every other
record-store domain. Every figure in the design doc that a sibling doc would
have confirmed against `products.json` is instead marked as an assumption, with
its failure mode. The five checks in "Verification still owed" are still owed.

**Every code defect on this branch was found in review; none by running the
crawler.** No total is given, because it kept growing after this log was
written and a fixed number here just goes stale — see the PR's resolved threads
for the full sequence. In rough order: the `vendor` fallback made the no-artist guard dead code; the dash
fallback bypassed the format gate; the Stock browser hardcoded `$`; count-
prefixed (`2CD`) and multiplier (`2xCD`) formats slipped the gate; spelled inch
markers (`10 INCH`) lost the vinyl override; mixed-format products published
their CD variants as vinyl; `Digital` was absent from the denylist; merch
suffixes bypassed the dash path.

Most of them are one bug: **a gate that covers one path while a second path
around it goes ungated**, or two expressions that are supposed to share a
vocabulary drifting apart. Later rounds added a second recurring shape, in the
dash-path splitter: finding the *wrong end* of the trailing format run — first
its leftmost token, then its last token rather than its last run, then a rule
so strict it stopped matching qualifier words. Two of those three were
regressions introduced by the fix before them. The design doc now states the invariant explicitly —
`_VINYL_RE`, `_NON_VINYL_RE` and `_FORMAT_TOKEN_RE` are maintained as one
vocabulary — because arriving at it took five separate rounds.

Three further corrections were to safety claims in the design doc itself, all
the same mistake: asserting a property from how the crawler behaves in
isolation without checking what the pipeline does with the result. Disabling a
store does **not** remove its rows; an unreadable feed does **not** fail
loudly, it deletes what is already there; not every unknown fails safe.

## Follow-ups not taken here

- **Disabled-by-default registration.** `register_crawler` inserts
  `enabled = TRUE`; the repo owner accepted shipping enabled, knowing the
  rollback is manual.
- **Detection for a zero-item catalog crawl.** Would change shared sync
  behaviour for all `catalog` crawlers, several of which legitimately return
  zero.
- **The dash path's album/format ambiguity.** `Artist - The Tape` is
  indistinguishable from `Artist - Album TAPE`; documented as accepted.
- **The same multi-variant identity collision in `centurymedia.py`,
  `napalmrecords.py` and `peaceville.py`**, which share the unqualified-title
  shape this crawler was corrected away from.
