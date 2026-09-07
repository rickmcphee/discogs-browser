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
  desktop table); `ReasonPopover`, anchored to that button and holding the
  justification, rendered as its sibling so Tab reaches it, with its
  placement arithmetic in `frontend/src/views/reasonPopoverPosition.ts`;
  `titleTooltip()` loses its reason branch and the artist elements lose
  `title={item.reason}`. (The first PR shipped a centred modal here; the
  second replaced it — see the amendment at the top.)
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
- **The popup names the target, not the row** — but only where the row does
  not. Its subtitle rendered
  `item.title` rather than `displayTitle()`'s substituted name. A judgment is
  made against an `item_key`; a comparison row shows what its marketplace
  called the thing it matched, which the listing-title design exists because
  it can be another pressing. Crediting the reason to that name would
  attribute it to a record the judge never saw. The popover narrows this to
  the case that needs it — see "It names the record only where the row does
  not" in the amendment below.
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
- ~~**The opener is handed over, not looked up.**~~ *Superseded: opening moves
  no focus, so there is nothing to restore unless the panel was given some —
  and when it was, every dismissal path hands it back, not Escape alone. The
  icon is to hand either way.* The click passed its own `currentTarget` into the dialog's state
  rather than letting the dialog read `document.activeElement` on mount,
  because Safari does not focus a button on pointer activation. The click
  still hands over its `currentTarget`, but as the element the popover
  measures itself against.
- **The panel caps its size and scrolls.** A reason is free text — a CSV
  import writes it unbounded — so a long one would otherwise run off the
  screen. Both caps come from the placement and are applied inline: it is the
  only thing that knows what room the icon leaves and where the safe screen
  ends, and a cap beside it in CSS is a cap it can contradict — bounds
  enforced against a width or height the panel does not actually have put it
  somewhere it does not actually fit. `w-64` remains as the width it asks
  for. The measurement takes the caps off first: measuring a capped panel
  would keep it capped, so one opened on a narrow screen would never widen
  again when the screen did. Then the width alone goes back on — the room the
  screen leaves, from `reasonPopoverMaxWidth()`, the same cap the placement
  itself applies — before the height is read, because the text reflows to it
  and a height taken at the wider layout comes out short: the reason ends up
  scrolling inside a panel with vertical room going spare. Both caps go back
  on imperatively rather than by the render that follows, because the clear
  went behind React, which does not re-write a style value it believes is
  already applied. It is measured from the panel's content
  rather than its rendered box — the box carries the cap the last placement
  gave it, and reading that back would find room the panel does not have,
  lengthen it, and jitter on every scroll. Measuring instead of caching is
  what keeps a resize honest: a panel opened in a narrow viewport that then
  grows is re-measured rather than placed against the size it used to be.

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
  that side. It flips to the right only when the left cannot hold it *and*
  the right can — a flip that had to be clamped back would land on the icon,
  burying the control that closes it — and when neither side fits it stacks
  below, or above where below is short. Clamped to the viewport throughout.
  Where it fits neither whole — a landscape phone, or any viewport at high
  zoom — it takes the roomier side and reports the height that will hold it
  there, scrolling what it cannot show, rather than being clamped over the
  icon. The one case it will still cover the icon is a viewport barely taller
  than the icon itself, where the alternative is a sliver too short to read;
  Escape and a press outside remain. The viewport it clamps against is the
  *safe* one: `window.innerWidth`/`innerHeight` count a notch and a home
  indicator as usable screen, the app's own layout does not (`px-safe` in
  `index.css`, whose comment names the landscape-notch case), and a popover
  clamped under a notch is unreadable at exactly the edge it was pushed to.
  The insets come off a throwaway element carrying them as real padding —
  `env()` cannot be read from script, and a custom property holding one comes
  back unresolved.
- **Positioned fixed, measured after render.** The table and the card list
  are both `overflow-auto`, so a popover positioned inside them would be
  clipped for a row at the top or bottom edge — unrecoverably, since
  overflow above the container does not become scrollable. Fixed coordinates
  computed from the icon's own rect avoid that in every view, and are
  recomputed on resize (a scroll dismisses the popover rather than moving
  it — see below). The panel's size is an input to that placement, so it is measured
  in a layout effect and held hidden for the frame before it is placed.
  `placeReasonPopover` (`frontend/src/views/reasonPopoverPosition.ts`) is
  that arithmetic, pure and tested on its own — jsdom measures every element
  as a zero-sized box at the origin, so this geometry cannot be asserted
  through a rendered component.
- **A disclosure, not a dialog and not a tooltip.** Opening moves no focus
  and traps nothing. The icon owns the relationship — `aria-expanded` with
  `aria-controls`, plus `aria-describedby` so a screen reader on the icon
  hears the reason without travelling to it. The panel is `role="note"`:
  an ARIA tooltip is a non-focusable description shown on hover or focus,
  and this is click-controlled and deliberately takes a tab stop, which
  makes a focusable `tooltip` a pattern assistive tech has no good reading
  of. It takes that tab stop for one reason — the reason can outrun the
  panel, and Safari will not hand a scroll container to the keyboard on its
  own — which is also why it renders as the icon's sibling, so Tab from the
  icon reaches it. Focus is handed back to the icon on unmount when the panel
  had it — on unmount rather than in one dismissal path, because Escape, the
  icon's second click, a press outside and a refetch all remove the panel and
  only the first would otherwise restore it, and because Safari does not focus
  the icon on the click that closes it. A flag set on the panel's own
  focus/blur rather than a live `activeElement` read: focus has usually moved
  on by cleanup time, and where the browser moved it to the icon itself the
  blur clears the flag, so this never takes focus back from where it belongs.
  With `preventScroll`, since one of the ways this closes is the user
  scrolling — focusing an icon they have just scrolled away from would have
  the browser scroll it back and undo them.
  That is the whole of the focus handling: no trap, no restoration when the
  panel never had focus, no backdrop. The panel carries no `aria-label`: it is the
  `aria-describedby` target, and per accname a name on it would win the text
  alternative outright — the icon would describe itself as "Recommendation
  details" rather than reading out the justification, which is the whole
  point of the reference.
- **It closes when its icon goes away, or leaves the screen.** A view-mode or
  breakpoint switch rebuilds the row in a different tree, leaving the node the
  panel was measured against detached, and a detached node reports a zero rect
  the next scroll would turn into a jump to the viewport corner. A refetch
  that drops the row is worse than it looks: the popover unmounts with its
  icon and so cannot clear the state itself, and a row that came back would
  find that state still set and reopen unbidden. Both are handled in the
  parent, adjusting state during render the way the view-mode and
  hidden-crawler resets above it already do — the render that drops a row
  changes none of `StockBrowser`'s own inputs, so a dependency-listed effect
  would not run, and an effect without a list is the same thing with an extra
  pass and a lint warning. Scrolling the row out of sight closes it too: the
  panel usually names no record, so beside unrelated rows it would say
  nothing about where it came from. Rather than judge on every scroll whether the row is
  still *visible* — it can be hidden while still in the viewport, scrolled out
  of the table's own overflow container or under its sticky header, and
  occlusion in general is not something geometry answers — a scroll simply
  dismisses it. That is the simpler rule and the one that matches a glance:
  you moved on. A scroll inside the panel is the opposite, being how a long
  reason is read, and leaves it alone. The popover also closes itself when it
  finds its anchor detached: a view-mode switch mounts it afresh against the
  icon from the tree it replaced, a commit after the parent's own check could
  have seen that. And when it finds the anchor off the screen entirely, which
  a refetch can do without any scroll at all — a sync inserting rows above
  this one in the current sort moves it, and the placement re-runs on the new
  item. That check claims only "on screen", which geometry answers; whether
  the row is *visible* is the question a scroll dismisses rather than
  computes.
- **Dismissal listens for touch as well as mouse.** A tap emits `mousedown`
  only as a compatibility event, and a touch scroll emits none at all, so a
  mouse-only listener would leave the popover open on a phone. Both events,
  matching `StockFilter`.
- **It names the record only where the row does not.** The modal named it
  unconditionally, to keep a comparison row's substituted `listing_title`
  from crediting the judgment to a pressing the judge never saw. Being
  pinned to its row settles *which row*, which is not the same question: the
  row itself may be showing a source's own name for what it matched, and
  then the reason still reads as being about a record the judge never saw.
  So the line survives, on the rows that need it — `namesAnotherPressing()`,
  the predicate `titleTooltip()` already used, gates it — and everywhere
  else the popover is verdict and sentence, which is what a glance wants.
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
- The open icon reports `aria-expanded="true"` and points both
  `aria-controls` and `aria-describedby` at the popover's id; a closed one
  reports `aria-expanded="false"` and neither reference. Its accessible
  description resolves to the justification — documenting it rather than
  guarding it, since jsdom's description computation falls back
  to text content whether or not a name is present.
- The popover is positioned fixed, with coordinates and visibility set — the
  geometry itself belongs to `reasonPopoverPosition.test.ts`.
- Both its caps come from the placement rather than a class, and it is
  focusable, `role="note"`, and rendered as the icon's next sibling.
- Its width is measured with the caps off, so a panel narrowed once can widen
  again; its height is measured with the width cap back on, so the text has
  reflowed to the width it will get. Both asserted by watching what the caps
  are at the moment each is read.
- A touch outside it closes it, as a mouse press does.
- Escape pressed while the panel has focus returns that focus to the icon.
- A view-mode switch closes it, asserted through the icon's `aria-expanded`
  rather than the panel's absence: an unplaced panel is hidden, and a role
  query cannot tell that from closed.
- A refetch that drops the row closes it *and* leaves it closed when the row
  returns — absence while the list is empty proves nothing on its own.
- A scroll of the list closes it; a scroll inside the panel does not.
- A refetch that moves its row off the screen closes it, with no scroll
  involved — the fetches return fresh objects, as the real one does, since
  that is what re-runs the placement.
- Closing a focused panel by clicking its icon returns focus to that icon,
  not just closing by Escape.
- It leaves the record unnamed on a row that already names it, and names the
  target on a comparison row whose `listing_title` names another pressing.
- Resizing the window re-places it from the panel's current size and the new
  viewport, rather than from what either measured when it opened.
- A long reason scrolls inside it rather than growing it.
- Neither tile button is inside the listing link, and clicking one never
  reaches it.
- The info button sits before the save button in the row's actions.

`frontend/src/test/reasonPopoverPosition.test.ts` (new): the popover sits a
gap to the icon's left and centred on it; flips right when the left cannot
hold it and the right can; stacks below — clear of the icon's own band — on a
narrow screen where neither side fits, and above when below is short; still
sits beside an icon that has room, however narrow the screen; stays on screen
for an icon straddling any of the four viewport edges, since the containers
scroll in both axes and room beside a half-visible icon is not room on screen
(an icon *entirely* off screen never reaches the placement — the popover
closes first); is clamped at the top and bottom for a row at either edge
of the viewport, including a panel taller than the viewport itself; and
reports a `maxHeight` throughout — the height the panel asked for wherever
that fits, and on the roomier side of an icon that a short viewport leaves no
room beside, above or below, the room actually available there; the height
asked for again where even the roomier side is too short to be worth
reading; and stays out of the safe-area insets a notched screen reserves —
capping its own height and width to the room between them, since a cap taken
from the raw viewport lets through a panel bigger than the bounds being
enforced, and then every bound crosses its own start.

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
