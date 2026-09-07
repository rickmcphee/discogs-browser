# Recommendation info icon and justification popup design

Date: 2026-09-07
Branch: `claude/recommendation-info-icon-popup-6n4r87`

## Problem

A judged stock item's justification — `stock_item_judgments.reason`, written
by the judgment run or by a CSV import — had one route to the screen on a
desktop: the native browser tooltip on the row's artist and title text
(`title={item.reason}`). That route has three problems.

- **It is invisible.** Nothing on the row says a justification exists. A
  user only finds one by hovering a row that happens to carry it, so the
  feature is discovered by accident or not at all.
- **It has no touch equivalent.** A phone has no hover. The mobile card list
  works around this by printing the reason inline under the title, which is
  a second, differently-shaped answer to the same question.
- **It collides with the listing-title tooltip.** `titleTooltip()` had to
  rank two unrelated pieces of hover text against each other: a
  recommendation reason and the target title a substituted `listing_title`
  displaced (see
  [`2026-09-05-marketplace-listing-title-design.md`](2026-09-05-marketplace-listing-title-design.md)).
  The reason won, so the row that most needed the substitution visible — a
  recommended one — was the row that hid it.

The judgment's polarity never reached the browser at all. `reason` is
returned for a judgment of either polarity, so a negative judgment carrying
a reason (which a CSV import can write; the judgment prompt writes `null`)
rendered its text in a slot the UI presents as a recommendation.

## Scope

Touches:

- `backend/db.py` — `get_stock_items` and `_get_stock_offers` select
  `j.recommended` alongside `j.reason`; the grouped path's comparison rows
  inherit it from their own row, as they already do for `reason`.
- `frontend/src/api/types.ts` — `StockItem.recommended: boolean | null`.
  `null` is "no judgment", distinct from `false`.
- `frontend/src/views/StockBrowser.tsx` — an `InfoIcon` button immediately
  left of the save bookmark in all three views (tiles, mobile cards,
  desktop table); a modal holding the justification; `titleTooltip()` loses
  its reason branch and the artist elements lose `title={item.reason}`.
- `frontend/src/test/stockBrowser.test.tsx` also loses the assertion that the
  tile bookmark's `e.preventDefault()` stops the enclosing link, which the
  restructure makes moot.
- Tests: `frontend/src/test/stockBrowser.test.tsx`,
  `frontend/src/test/mobileLayout.test.tsx`,
  `backend/tests/test_stock_crud.py`.

Out of scope:

- **The judgment prompt.** `backend/recommendations_prompt.md` still says to
  write a reason only when recommending. Moving the justification behind a
  click removes the constraint that produced that rule ("the reason is
  displayed beside the item wherever it appears"), so writing reasons for
  rejections is now possible — but it is a change to what the model is asked
  to produce and to every future judgment's token cost, not a UI change, so
  it is left for its own decision. Until then the icon appears on positive
  judgments and on imported negative ones that carry a reason.
- **The mobile card's inline reason line.** Kept. It exists because touch
  has no hover; the popup now covers that, so the line is redundant rather
  than wrong, and removing it is a separate call about mobile row density.
- **`RecordBrowser`.** Judgments are a Store-tab concept; the Collection and
  Wantlist tabs render no reason.

## Decisions

- **The icon marks the justification, not the judgment.** It renders when
  `item.reason` is non-empty, not whenever a judgment row exists. Most
  judged items are rejections with a `null` reason, so keying the icon on
  the judgment would put one on nearly every row in the Store tab and open
  an empty popup from each. The icon's promise is "there is something to
  read here", and it only makes that promise when there is.
- **The popup names the polarity.** "Recommended" or "Not recommended" as
  the heading, from the new `recommended` field. Without it, a rejection's
  imported reason would render under recommendation framing — the exact
  misreading `recommendations_prompt.md` avoids by withholding the text.
  `null` (no judgment) cannot reach the popup: no judgment means no reason
  means no icon.
- **A modal, not a popover.** The justification is a sentence of prose with
  no anchor relationship to the row's other controls, and the same component
  has to work on a phone. A centred modal matches the ones `App.tsx`
  already renders, needs no positioning logic, and reads identically at
  every width. Escape and a backdrop click dismiss it; focus moves into the
  panel on open and returns to the icon that opened it on close.
- **The opener is handed over, not looked up.** The click passes its own
  `currentTarget` into the dialog's state rather than letting the dialog read
  `document.activeElement` on mount: Safari does not focus a button on
  pointer activation, so the lookup would find the body and give focus back
  to nothing. jsdom behaves the same way, which is what the test asserts on.
  A restore target that a refetch has since detached is skipped.
- **The panel caps its height and scrolls.** A reason is free text — a CSV
  import writes it unbounded — so on a short viewport a long one would push
  the Close button past the bottom edge of a panel that could not scroll.
- **The icon is the row's third action, in the actions group.** Immediately
  left of the bookmark in each view, so the row's controls stay in one
  place: cost link, info, save. In tiles the bookmark is an overlay on the
  cover, so both buttons share one absolutely-positioned flex row — and that
  row moved out of the tile's listing link, which had been wrapping it. A
  control inside a control is invalid whatever its click handler does, and
  an info button that exists to be reachable where a tooltip was not cannot
  sit in a structure assistive tech may decline to expose. The tile is now a
  plain wrapper over two siblings, the link and the action group; the
  `e.preventDefault()` that used to hold the nesting together is gone with
  it. See the amendment in
  [`2026-08-16-store-saved-items-design.md`](2026-08-16-store-saved-items-design.md),
  which specified the nested form.
- **`titleTooltip()` keeps only the substitution.** With the reason gone
  from hover text, the title tooltip does what its own design asked for
  unconditionally: it shows the target title whenever `listing_title`
  displaced it. The ranking that design settled ("a reason occupies the
  tooltip, it wins, since it did before") no longer has two claimants.

## Testing

`frontend/src/test/stockBrowser.test.tsx`:

- The tooltip tests are inverted: an item with a reason renders no
  `title` on its artist cell or its tile-view artist text, in either view.
- An item with a reason renders an info button; one without renders none.
- Clicking the info button opens a dialog holding the reason, headed
  "Recommended" for `recommended: true` and "Not recommended" for `false`.
- The dialog closes on its Close button and on Escape, and closing hands
  focus back to the info button — a click never focuses it in jsdom, so this
  fails outright if the dialog looks its opener up instead of being given it.
- The panel is capped and scrollable rather than able to overflow a short
  viewport.
- Tab and Shift+Tab stay inside the dialog from every starting point the
  trap branches on — the panel itself, the first and last controls, and
  focus that has escaped it entirely.
- Neither tile button is inside the listing link, and clicking one never
  reaches it.
- The info button sits before the save button in the row's actions.

`frontend/src/test/mobileLayout.test.tsx`:

- The card's info button opens the same dialog, and its touch target is
  44px.

`backend/tests/test_stock_crud.py` (where the rest of `get_stock_items`'
per-row payload is covered):

- `get_stock_items` returns `recommended` alongside `reason`, for a
  positive and a negative judgment, and `None` for an unjudged item.
- Comparison rows carry the same verdict and reason as the row they hang
  under, on both the grouped path and the flat Cost-sort one — they are
  built from separate SELECTs.

## Spec drift

- [`2026-07-06-store-recommended-filter-design.md`](../../superpowers/specs/2026-07-06-store-recommended-filter-design.md)
  describes the reason as a `title` attribute on the artist/title cells and
  lists "hovering a recommended row shows the reason" as a user-visible
  behaviour. Amended to point here.
- [`2026-09-05-marketplace-listing-title-design.md`](2026-09-05-marketplace-listing-title-design.md)
  describes the reason as outranking the target title in the tooltip.
  Amended: there is one claimant now.
- [`2026-08-27-mobile-web-experience-design.md`](2026-08-27-mobile-web-experience-design.md)
  describes the card list but never the inline reason line, so nothing
  there drifted. The line's rationale lives only in its test name, which
  this branch updates in place.
