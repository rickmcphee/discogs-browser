# Recommendation info icon and justification popup design

Date: 2026-09-07
Branch: `claude/recommendation-info-icon-popup-6n4r87`

**Amendment (2026-09-07, same branch, second PR — the first merged as #320):**
the justification is shown in an **anchored popover**, not the centred modal
this document argued for. The modal read as too formal for a glance at one
sentence: it dimmed the page, took focus, and asked for a deliberate Close.
The popover opens on a click of the info icon, closes on a second click of
that same icon, and sits to the icon's left. Everything else here stands —
what the icon means, when it appears, the verdict it carries, where it sits
in the row. The reversed and superseded parts are marked inline below.

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
  desktop table); a modal holding the justification (an anchored popover
  from the second PR, `ReasonPopover`, with its placement arithmetic in
  `frontend/src/views/reasonPopoverPosition.ts`); `titleTooltip()` loses
  its reason branch and the artist elements lose `title={item.reason}`.
- `frontend/src/test/stockBrowser.test.tsx` also loses the assertion that the
  tile bookmark's `e.preventDefault()` stops the enclosing link, which the
  restructure makes moot.
- Tests: `frontend/src/test/stockBrowser.test.tsx`,
  `frontend/src/test/mobileLayout.test.tsx`,
  `backend/tests/test_stock_crud.py`, and (second PR)
  `frontend/src/test/reasonPopoverPosition.test.ts`.

Out of scope:

- **The judgment prompt.** `backend/recommendations_prompt.md` still says to
  write a reason only when recommending. Moving the justification behind a
  click removes the constraint that produced that rule ("the reason is
  displayed beside the item wherever it appears"), so writing reasons for
  rejections is now possible — but it is a change to what the model is asked
  to produce and to every future judgment's token cost, not a UI change, so
  it is left for its own decision. Until then the icon appears on positive
  judgments and on imported negative ones that carry a reason.
- *(Reversed during review — see the Decisions entry below.)* The mobile
  card's inline reason line was kept at first, on the grounds that it
  answers a different need (touch has no hover) and that removing it was a
  call about row density this change didn't have to make.
- **`RecordBrowser`.** Judgments are a Store-tab concept; the Collection and
  Wantlist tabs render no reason.

## Decisions

- **The icon marks the justification, not the judgment.** It renders when
  `item.reason` is non-empty, not whenever a judgment row exists. Most
  judged items are rejections with a `null` reason, so keying the icon on
  the judgment would put one on nearly every row in the Store tab and open
  an empty popup from each. The icon's promise is "there is something to
  read here", and it only makes that promise when there is.
- **The popup names the target, not the row.** Its subtitle renders
  `item.title` rather than `displayTitle()`'s substituted name. A judgment is
  made against an `item_key`; a comparison row shows what its marketplace
  called the thing it matched, which the listing-title design exists because
  it can be another pressing. Crediting the reason to that name would
  attribute it to a record the judge never saw.
- **The popup names the polarity.** "Recommended" or "Not recommended" as
  the heading, from the new `recommended` field. Without it, a rejection's
  imported reason would render under recommendation framing — the exact
  misreading `recommendations_prompt.md` avoids by withholding the text.
  `null` (no judgment) cannot reach the popup: no judgment means no reason
  means no icon.
- ~~**A modal, not a popover.**~~ *Reversed — see the amendment below.* The
  justification is a sentence of prose with no anchor relationship to the
  row's other controls, and the same component has to work on a phone. A
  centred modal matches the ones `App.tsx` already renders, needs no
  positioning logic, and reads identically at every width. Escape and a
  backdrop click dismiss it; focus moves into the panel on open and returns
  to the icon that opened it on close.
- ~~**The opener is handed over, not looked up.**~~ *Superseded: nothing takes
  focus now, so there is none to give back.* The click passed its own
  `currentTarget` into the dialog's state rather than letting the dialog read
  `document.activeElement` on mount, because Safari does not focus a button on
  pointer activation. The click still hands over its `currentTarget`, but as
  the element the popover measures itself against.
- **The panel caps its height and scrolls.** A reason is free text — a CSV
  import writes it unbounded — so a long one would otherwise run off the
  screen. (`max-h-64` on the popover, where the modal used `max-h-[85dvh]`.)

**Amendment (2026-09-07, second PR): a popover, opened and closed by its own
icon.**

- **The icon is the whole control.** A click opens the popover, a second
  click on the same icon closes it, and a click on another row's icon moves
  it there rather than opening a second. Escape closes it, and so does a
  press anywhere outside it — but never a press on the icon itself, which
  belongs to the toggle: dismissing there would leave the click that follows
  to reopen what it was meant to close. There is no Close button, because
  the thing that opened it is always right there.
- **Anchored to the icon's left.** That is where the space is: the icon sits
  in the row's right-hand action group, so the row's own width is free on
  that side. It flips to the right only when the left cannot hold it, and is
  clamped to the viewport on both axes.
- **Positioned fixed, measured after render.** The table and the card list
  are both `overflow-auto`, so a popover positioned inside them would be
  clipped for a row at the top or bottom edge — unrecoverably, since
  overflow above the container does not become scrollable. Fixed coordinates
  computed from the icon's own rect avoid that in every view, and are
  recomputed on scroll (capturing, so the table's own scroll counts) and on
  resize. The panel's size is an input to that placement, so it is measured
  in a layout effect and held hidden for the frame before it is placed.
  `placeReasonPopover` (`frontend/src/views/reasonPopoverPosition.ts`) is
  that arithmetic, pure and tested on its own — jsdom measures every element
  as a zero-sized box at the origin, so this geometry cannot be asserted
  through a rendered component.
- **It takes no focus and holds nothing focusable.** A glance at one sentence
  should not move the caret or trap Tab. It stays out of the tab order and
  reaches assistive tech through `aria-describedby` on the icon, with
  `aria-expanded` saying whether it is open — the disclosure pattern for a
  control that reveals text rather than a dialog that owns interaction. This
  is what retires the focus trap, the focus restoration and the backdrop.
- **It does not name the record.** The modal did, to keep a comparison row's
  substituted `listing_title` from crediting the judgment to a pressing the
  judge never saw. A popover pinned to the row it belongs to cannot be
  ambiguous about which row that is, so the line goes and the hazard with
  it.
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
- **The mobile card no longer prints the reason inline.** It was the
  stand-in for a hover a touch device cannot perform, and the info button is
  that stand-in now — on the same row, in the same action group as every
  other view. Keeping both would be merely redundant; what settles it is
  that the line carried no verdict, so a rejection's imported note read as a
  recommendation there — the exact misreading the popup's heading exists to
  prevent, left standing on the one surface that had a second copy of the
  text.
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
- Clicking the info button opens a popover holding the reason, labelled
  "Recommended" for `recommended: true` and "Not recommended" for `false`.
- A second click on the same icon closes it — with the press that precedes
  that click fired too, since the outside-dismiss listener sees it first and
  has to let it through.
- Escape closes it, and so does a press outside it; a press inside it does
  not.
- A click on another row's icon moves the popover rather than opening a
  second one.
- The open icon reports `aria-expanded` and points `aria-describedby` at the
  popover's id; a closed one reports neither.
- The popover is positioned fixed, with coordinates and visibility set — the
  geometry itself belongs to `reasonPopoverPosition.test.ts`.
- It does not name the record, on a comparison row carrying a
  `listing_title` least of all.
- A long reason scrolls inside it rather than growing it.
- Neither tile button is inside the listing link, and clicking one never
  reaches it.
- The info button sits before the save button in the row's actions.

`frontend/src/test/reasonPopoverPosition.test.ts` (new): the popover sits a
gap to the icon's left and centred on it; flips right when the left cannot
hold it; is pulled back in rather than overhanging the right edge after that
flip; and is clamped at the top and bottom for a row at either edge of the
viewport, including a panel taller than the viewport itself.

`frontend/src/test/mobileLayout.test.tsx`:

- The card's info button opens the same popover and closes it on a second
  click, and its touch target is 44px.
- A card with a reason does not render it inline, and the popover it opens
  labels a `recommended: false` item "Not recommended".

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
  there drifted when the line was removed. Its rationale lived only in its
  test name, which this branch rewrites in place.
- [`2026-08-16-store-saved-items-design.md`](2026-08-16-store-saved-items-design.md)
  also called the bookmark one of the mobile card's *two* right-hand actions
  beside the cost link — true until the info button joined them. Caught in
  review, amended in place, along with the same claim in the card list's own
  code comment.
- [`2026-08-08-store-collection-split-design.md`](2026-08-08-store-collection-split-design.md)
  prints a `StockItem` interface that ends at `reason`/`is_own`. Missed on
  the first sweep and caught in review; amended. It had already fallen
  behind by three fields before `recommended`, so the amendment names them
  and points at `types.ts` as the live shape rather than restating it.
