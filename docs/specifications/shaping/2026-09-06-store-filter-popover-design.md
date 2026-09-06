# Store and Track "Filter" popover design

Date: 2026-09-06
Branch: `claude/store-cheapest-filter-x4tdwl` (second PR from this branch
name; the first, the Cheapest filter, merged as PR #294)

**Amendment (2026-09-06, same PR):** the Track option set described below lasted one commit. The Track tab folded into Store in the next, so `StockFilter` has no `scope` prop and one option set — All / Recommended / Saved / Overlapped / Collection / Wantlist — with Cheapest always present. See [`2026-09-06-track-tab-fold-design.md`](2026-09-06-track-tab-fold-design.md).

## Problem

The Store toolbar's control group had grown to `Source · Stats · <select> ·
☐ Cheapest · list · tiles`, plus `Artist` and `Sort` on a phone. The
Cheapest checkbox fitted, but it took the row's last free width: the next
filter would not. [`2026-09-05-store-cheapest-filter-design.md`](2026-09-05-store-cheapest-filter-design.md)
shipped the checkbox beside the dropdown and left the fix as a proposal
under "Toolbar real estate". This is that fix.

The dropdown itself was also the odd one out. `Source` and `Stats` beside
it are both triggers that open a panel — an anchored dropdown above the
breakpoint, a `Sheet` below it — while the row-set filter was a bare
`<select>` and the Cheapest toggle a bare checkbox, two controls for what is
one question ("which rows").

## Scope

Touches:

- `frontend/src/components/StockFilter.tsx` — new: the `Filter: …` trigger
  and its panel, holding the scope radios (Store: All / Recommended / Saved
  / Overlapped; Track: All / Collection / Wantlist) and, on Store, the
  Cheapest checkbox.
- `frontend/src/views/StockBrowser.tsx` — the `<select>` and the Cheapest
  `<label>` are replaced by one `<StockFilter>`; no state, effect or handler
  changes. `selectClass` is no longer imported.
- Tests: `frontend/src/test/stockFilter.test.tsx` (new),
  `frontend/src/test/stockBrowser.test.tsx` and
  `frontend/src/test/inStockTab.test.tsx` (every query that reached for the
  `<select>` or its options now goes through the popover).

Out of scope:

- **Folding `Source` in.** Considered and left where it is. Source's panel
  is already the tallest in the toolbar (genre tri-states plus a per-store
  list that scrolls at `max-h-[28rem]`), and it lights up on its own
  condition — a narrowed source set — that has nothing to do with the
  row-set filter. Putting both behind one trigger would make one panel
  scroll for two unrelated questions and lose the separate "you are hiding
  stores" signal. The row this frees is enough for now; if a fourth filter
  arrives, revisit.
- **`Stats`.** A readout, not a filter — the source-stats design kept it
  separate for exactly that reason.
- **`RecordBrowser`'s Plex dropdown.** The Collection tab's
  `all`/`unmatched` `<select>` is a one-bit filter with nothing to share a
  panel with. Left alone.
- **Any backend or API change.** None. The values sent are the same
  `filter` and `cheapest` state as before.

## Decisions

- **One panel, the same shape as its neighbours.** `StockFilter` is built
  on the pattern `SourceFilter` and `StockStats` already share: a trigger
  styled by `navButtonClass`, `aria-expanded`, an `absolute right-0 … w-56`
  dropdown above the breakpoint dismissed by outside click, and a `Sheet`
  below it — see the mobile design's "an anchored dropdown cannot be made
  safe on a wrapping toolbar" decision, which applies here unchanged. Three
  controls in one row that open the same way is the consistency the
  toolbar was missing.

- **The trigger reads its state.** A `<select>` shows its value at a glance,
  and that must not be lost behind a button labelled `Filter`. So the
  trigger reads `Filter: All`, `Filter: Saved`, `Filter: Saved · Cheapest`
  — the same `Label: value` shape as the mobile `Artist: …` button — and
  lights up (`navButtonClass(true)`) whenever the state is not the default,
  as `Source` does when it is narrowing the view. `max-w-48 truncate` bounds
  the longest reading (`Filter: Recommended · Cheapest`).

- **Radios, not a nested select.** The scope is a single choice, which is
  what a radio group is; a `<select>` inside a panel would be a dropdown
  inside a dropdown. `Recommended` keeps its disabled state and the reset
  effect in `StockBrowser` (back to All when recommendations stop being
  available) is untouched, since the component only reflects `filter`.

- **The panel stays open after a choice.** Store's panel holds two
  independent controls — the scope and Cheapest — and the common gesture is
  "Saved, and cheapest": closing on the first click would cost a reopen.
  This is also what `Source` does. Outside click, Escape (via `Sheet` on a
  phone) and the trigger close it.

- **Cheapest is Store-only in the panel and in the trigger.** The Track
  panel has no checkbox, and the trigger never reads ` · Cheapest` under
  `scope="track"` even if the prop is passed, so a stored Cheapest choice
  cannot show up on a tab it does not apply to. `StockBrowser` already
  gates the state the same way.

- **A one-line hint under Cheapest.** "One row per record: the lowest-priced
  store that has it." The checkbox's meaning is not self-evident from one
  word, and the panel has the room the toolbar never did.

## Testing

`stockFilter.test.tsx`: the trigger reads the current filter and renders no
panel until clicked; the Store panel lists All/Recommended/Saved/Overlapped
in order with Cheapest beneath, the Track panel lists
All/Collection/Wantlist without it and never reads Cheapest into its
trigger; Recommended is disabled until available; a choice is reported and
the panel stays open; the Cheapest toggle is reported; a non-default state
(scope, Cheapest alone, or both) reads into the trigger and lights it, the
default does not; the desktop panel closes on outside click and on the
trigger; on a phone the panel is a `Sheet` dialog named "Filter" with its
own Close.

`stockBrowser.test.tsx`: every existing test that changed or read the
`<select>` now does so through helpers that open the popover and click a
radio, or read the value back off the trigger's text — the behaviour under
test (which request each choice sends, the sidebar refetch, the empty-state
copy, the stored-value restore, the Recommended reset, the Cheapest
persistence) is unchanged. `inStockTab.test.tsx`'s Recommended-availability
tests open the Store trigger and read the radio's disabled state; with
Store and Track both mounted there are two triggers, and only Store's panel
carries a Recommended radio.

Nothing here touches the backend; no Python test changes.

## Spec drift

Grepped both spec trees for the Store/Track filter dropdown, its `<option>`
set and `STORE_FILTERS`. Every spec that describes the control as a
`<select>` or dropdown gets a dated note pointing here; the values and their
behaviour those specs define are unchanged, only the control that presents
them:

- [`2026-09-05-store-cheapest-filter-design.md`](2026-09-05-store-cheapest-filter-design.md)
  — its "Toolbar real estate" section proposed this; noted as done, with
  the `Source` fold-in left as the section's remaining option.
- [`docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`](../../superpowers/specs/2026-07-05-in-stock-crawler-design.md)
  — "a filter dropdown sits left of the list/tile toggle".
- [`docs/superpowers/specs/2026-07-06-store-recommended-filter-design.md`](../../superpowers/specs/2026-07-06-store-recommended-filter-design.md)
  — the disabled `<option value="recommended">`.
- [`docs/superpowers/specs/2026-08-22-live-recommended-filter-design.md`](../../superpowers/specs/2026-08-22-live-recommended-filter-design.md)
  — "stays selectable in the Store tab's filter dropdown".
- [`2026-08-10-collection-wishlist-filter-design.md`](2026-08-10-collection-wishlist-filter-design.md)
  — the Track `<select>` and its default.
- [`2026-08-16-store-saved-items-design.md`](2026-08-16-store-saved-items-design.md)
  — the `Saved` option in the dropdown.
- [`2026-08-26-store-overlapped-artist-filter-design.md`](2026-08-26-store-overlapped-artist-filter-design.md)
  — "the Store branch of the dropdown gains one more option".
- [`2026-08-27-mobile-web-experience-design.md`](2026-08-27-mobile-web-experience-design.md)
  — the desktop/mobile table gains a row for the row-set filter, beside the
  Source filter's.

No spec touched carried a crawler/store/source/plugin/test count in the
passages amended.

## Runtime/agent document impact

No `.agents/` directory exists in this repo. This is a frontend-only change
with no new endpoint, parameter, or trust boundary. `README.md` and
`CLAUDE.md` need no change.
