# Mobile web experience design

Date: 2026-08-27
Branch: `claude/mobile-optimized-web-qmv4u4`

## Problem

The SPA has only ever been laid out for a desktop window. On a phone every
screen fails in a different way, and none of them are cosmetic:

- **The header nav overflows.** `App.tsx`'s header is one non-wrapping flex
  row: the library tabs on the left, the admin tabs plus the avatar
  pushed right by `ml-auto`. At 390 px the right-hand group is simply off
  screen, so an admin cannot reach Settings and *no* user can reach their
  own profile.
- **The artist sidebar eats the screen.** `RecordBrowser` and `StockBrowser`
  both open with `<aside className="w-48 … shrink-0">`. 192 px of a 390 px
  viewport is half the width, permanently, spent on a filter that is set once
  and then read never.
- **The tables are wider than the phone.** Collection/Wantlist render eight
  columns, Store/Track seven. `<table className="w-full">` inside
  `overflow-auto` does not wrap — it lays out to its natural width and the
  user side-scrolls a viewport-and-a-half to read one row.
- **`h-screen` is the wrong unit.** `100vh` on mobile Safari and Chrome is the
  height with the URL bar *retracted*, so the bottom of the app — which is
  where the fixed sync/crawl status bars live — sits under the browser chrome
  on load.
- **Focusing any input zooms the page.** iOS Safari auto-zooms a focused form
  control whose font-size is under 16 px. Every input in the app is `text-sm`
  (14 px), so tapping the search box zooms in and leaves the layout shifted.
- **Tap targets are mouse-sized.** Nav pills are `py-1.5`, icon buttons
  `p-1.5`, pagination `px-2 py-1` — 24–28 px boxes against the ~44 px both
  platform guidelines ask for.
- **Nothing accounts for safe areas.** No `viewport-fit=cover`, no `env()`
  padding, so on a notched phone content runs under the home indicator.

## Scope

Touches:

- `frontend/index.html` — `viewport-fit=cover`, `theme-color`, iOS web-app
  meta.
- `frontend/src/index.css` — safe-area padding utilities, the 16 px
  form-control floor on coarse pointers, `#root`'s desktop-only side
  borders and width clamp, tap-highlight and overscroll behaviour.
- `frontend/src/hooks/useMediaQuery.ts` — **new.** `useMediaQuery(query)` and
  the `useIsMobile()` wrapper.
- `frontend/src/App.tsx` — `h-dvh`; header splits into a mobile bar (title,
  admin overflow menu, avatar) and the unchanged desktop nav; a bottom tab bar
  for the library tabs on mobile; modals and status bars sized for a
  phone.
- `frontend/src/views/RecordBrowser.tsx`, `frontend/src/views/StockBrowser.tsx`
  — artist sidebar becomes a sheet behind a toolbar button, the toolbar
  reflows to two rows, the table becomes a card list, and a sort control
  appears to replace the column headers the card list has no room for.
- `frontend/src/views/Settings.tsx`, `frontend/src/views/Account.tsx` — the
  label/control/description layout tables stack under the breakpoint.
- `frontend/src/views/LogViewer.tsx` — the fixed-width column strip scrolls
  horizontally as one unit instead of crushing the message column.
- `frontend/src/views/QueueView.tsx` — donut/legend/table row stacks, stat
  tiles stop forcing a `min-w-36` grid wider than the screen.
- `frontend/src/components/SourceFilter.tsx` — the panel becomes a sheet
  below the breakpoint; the desktop dropdown is untouched.
- `frontend/src/views/LoginScreen.tsx`, `InviteCodeScreen.tsx` — card width
  becomes fluid.
- Tests: `frontend/src/test/mobileLayout.test.tsx` (new),
  `frontend/src/test/setup.ts` (a `matchMedia` stub).

Out of scope:

- **Any change to the desktop layout**, where *desktop* means the environment
  that was regression-tested: a pointer-fine browser reporting zero safe-area
  insets. There, every rule added here is gated behind `md:`/`useIsMobile()`
  or is a no-op, and the rendering that ships today is the rendering that
  ships after. Two rules deliberately reach past 768 px, and the word
  *desktop* rather than *≥ 768 px* is doing that work: a touch device at any
  width gets the 16 px form-control floor, and a device reporting a bottom
  inset gets it honoured (`md:pb-safe`) — neither failure is about layout
  width, so neither is gated on it. See the capability-versus-width decision
  below. `h-dvh` likewise replaces `h-screen` unconditionally, and resolves
  to the same height wherever no browser chrome retracts.
- **A PWA.** No manifest, no service worker, no offline mode, no install
  prompt, no push. `apple-mobile-web-app-*` meta tags are included because
  they cost two lines and fix the status-bar colour when someone adds the
  page to their home screen; nothing else about installability is addressed.
- **A native app or any wrapper.**
- **List virtualisation.** `PER_PAGE` stays 250 and the card list renders all
  250. Worth revisiting if it drags on a low-end phone, but that is a
  performance change with its own measurements, not part of a layout pass.
- **Responsive images.** Cover art keeps whatever URL the API returns; no
  `srcset`, no thumbnail service.
- **Backend changes.** Nothing here touches Python. No new endpoint, no new
  field, no new query parameter.

## Decisions

- **One breakpoint, at 768 px, and it is Tailwind's `md`.** The layout has
  exactly two states — "the artist sidebar and a seven-column table fit" and
  "they do not" — so a second breakpoint would buy a third variant of every
  view for no reader. 768 px is where the sidebar plus the narrowest useful
  table stops fitting, and reusing `md` means the class prefix and the
  JavaScript media query cannot drift apart. The query string lives in one
  place, `MOBILE_QUERY` in `useMediaQuery.ts`.

- **CSS first; JavaScript only where the two layouts cannot share a DOM.**
  Padding, font size, wrapping, column stacking, modal width — all of it is
  `md:` prefixes, which need no JavaScript, survive a resize for free, and
  cannot desynchronise from the rendered tree. `useIsMobile()` is reserved
  for the places where mobile needs *different elements*, not
  differently-styled ones: the nav, the artist sidebar, the row list, and the
  source filter's panel.

- **Where the DOM must differ, render one or the other — never both with one
  `hidden`.** The tempting version of a bottom tab bar is a second `<nav>`
  marked `hidden md:flex` alongside the first. That puts two buttons named
  "Settings", two named "Collection", and two copies of every artist and
  every row into the accessibility tree at once. Screen readers announce
  both, in-page find matches both, and the existing suite's
  `getByRole('button', { name: 'Settings' })` throws on the ambiguity rather
  than failing a meaningful assertion. Rendering one branch keeps the tree
  honest for everyone.

- **`useIsMobile()` returns `false` when `window.matchMedia` is missing.**
  That is jsdom's default, so every test that does not opt in keeps
  rendering the desktop tree it was written against, and the mobile tests
  opt in by stubbing `matchMedia`. The fallback is also the right production
  answer: a browser without `matchMedia` is a browser too old to be a phone
  this app supports, and desktop is the layout that degrades gracefully.

- **The initial value is read synchronously in the `useState` initialiser**,
  not in an effect. An effect-based read renders the desktop tree first and
  swaps it on mount, which on a phone is a visible flash of the layout the
  user cannot use.

- **A bottom tab bar for the library tabs.** Collection, Wantlist,
  Store and Track are the app — everything else is configuration. A bottom
  bar puts every one of them in the thumb's reach, keeps them visible (a hamburger
  hides the app's entire structure behind one glyph), and gives each a
  target the height of the bar rather than the height of a text pill.

- **The bar is a flow child of the shell's flex column, not `position:
  fixed`.** The root is already `flex flex-col` at viewport height, so a flow
  child shrinks `<main>` by exactly its own height. Fixed positioning would
  need every scroll container to carry matching bottom padding, and that
  padding would then have to include `env(safe-area-inset-bottom)` in every
  one of those places.

- **The status bars join the flow too, on mobile.** They were fixed, anchored
  above the tab bar by a `--app-bottom-inset` variable — and a fixed banner
  covers whatever is beneath it, which here is `<main>`'s last rows and its
  pagination. While a sync runs the banner has no Dismiss button, and the
  scroll container's bottom is already `main`'s bottom, so the covered rows
  could be neither dismissed nor scrolled clear. As flow children between the
  content and the tab bar they shorten `main` by exactly their own height.
  Reserving that height as a constant instead would not have worked: the
  banner wraps on a narrow screen, so its height is a property of the message,
  not of the layout. Desktop keeps the fixed overlay it has always had, at
  `bottom-0`, and the inset variable is gone — with nothing left outside the
  flow, there is nothing to offset.

- **Admin tabs go behind an overflow menu; the avatar stays in the header.**
  Queue, Logs and Settings are admin-only and rarely visited, which is
  exactly what an overflow menu is for. The avatar stays top-right where a
  profile control belongs on every platform, and it is the only header
  control a non-admin has, so the header does not collapse to nothing.

  **Amendment (2026-08-28, branch `claude/notifications-tab-price-alerts-rvdnfu`):**
  the avatar is no longer the only header control a non-admin has — a
  notification bell now sits immediately to its left, on mobile and desktop
  alike, for every user. It went there rather than into the bottom tab bar
  precisely because of this document's reasoning about that bar: a fifth
  thumb-width tab would squeeze the four that actually browse records. The
  header's mobile bar is therefore title, admin overflow menu (admin only),
  bell, avatar. See
  [`2026-08-28-price-drop-notifications-design.md`](2026-08-28-price-drop-notifications-design.md).

- **An anchored dropdown cannot be made safe on a wrapping toolbar, so the
  source filter becomes a sheet too.** `absolute right-0` aligns to the
  *trigger*, and below the breakpoint that trigger wraps to wherever the
  toolbar has room. From a left-edge position a 288 px panel starts at a
  negative x — measured at −203 px on a 390 px viewport, with most of the
  store list unreachable. Clamping the width does not help, because the
  overflow is on the *left*. Alignment chosen from the trigger's bounds would
  work, but it is bespoke positioning logic to reproduce what a sheet gets
  from being anchored to the viewport instead — and the panel is a long
  scrollable list of things to pick, which is the artist filter's shape
  exactly. Desktop keeps the anchored dropdown at its original `w-72`.

- **Every sheet carries a named close button.** The backdrop is pointer-only
  (`aria-hidden`, untabbable) so the focus trap has nothing to reach around,
  and the grab handle is decoration that implements no dragging — which
  leaves Escape as the only way out, and a switch or voice-control user may
  not have Escape. The artist and admin sheets close on selection anyway; the
  source sheet is a multi-select that deliberately stays open when an option
  is toggled, so there it is a trap rather than an inconvenience.

- **`aria-modal` obliges a real focus trap.** Focus moves into the panel on
  open, cycles within it on Tab and Shift+Tab, and returns to the invoking
  element on close. The alternative — leaving focus outside while claiming
  modality — is worse than not claiming it, because a screen reader acts on
  the claim and stops announcing the app the keyboard can still reach. The
  app behind is *not* additionally marked `inert`: the sheet renders inside
  the app root, so marking that root inert would freeze the sheet with it,
  and doing it properly means portalling the sheet out first.

- **The artist filter becomes a sheet, and its trigger shows the current
  selection.** A filter that is invisible until you open something is a
  filter users blame the data for, so the button reads `Artist: All` or
  `Artist: NAILS` rather than a bare icon.

- **The table becomes cards, not a horizontally-scrolling table.** Both are
  legitimate answers; cards win here because the columns are not peers.
  Artist and title are what the row *is*; year, label, format, price and
  source are annotations on it. A card can say that with hierarchy — cover,
  then artist, then title, then a dimmed meta line — where a side-scrolling
  table just makes the reader hold column positions in their head across
  two swipes.

- **Cards need a sort control, because the column headers carried it.**
  Sorting is not decoration here — an unsorted 250-row page is unusable — so
  the mobile toolbar gets a `<select>` of the same sort fields the headers
  expose plus a direction toggle. Both write the same `sort`/`order` state
  through the same `toggleSort`, so there is one sort model, not two.

- **Settings and Account keep their `<table>` markup and stack with
  `block md:table-row`.** Those tables are layout, not data — label, control,
  description — so `display: block` on the rows and cells under the
  breakpoint gives a stacked form with no markup change at all. Rewriting
  them as grids would have been marginally cleaner markup in exchange for
  rewriting every `.closest('tr')` in the suite, on tables whose semantics
  were never the point.

- **A 16 px floor on form controls, applied globally in `index.css` and gated
  on pointer capability.** The alternative is `text-base md:text-sm` on every
  input, select and textarea in the app, which is the same rule written a few
  dozen times and forgotten on the next one added — exactly the case a global
  element rule is for. The gate is `(pointer: coarse)`, *not* a width, because
  zoom-on-focus is a property of the device rather than of the layout: a touch
  device wider than the breakpoint — a tablet, or a phone in landscape — still
  zooms, and a width gate stops applying to it. Measured: with a width gate a
  touch device at 1024 px receives 14 px controls; with the capability gate it
  receives 16 px, while a mouse-driven desktop is untouched at either.

- **Capability gates for hard failures, width gates for affordances.** The two
  are not interchangeable and the line is worth stating, because a phone above
  the breakpoint runs the desktop layout. Zoom-on-focus and content sliding
  under the home indicator are *breakage* — the page ends up zoomed or the
  content unreadable — so they follow the device: `(pointer: coarse)` and
  `env()` respectively. Touch-target sizing is an *affordance* of the mobile
  layout, so it follows the breakpoint: a phone in landscape is showing the
  desktop layout, with the sidebar and the table, and its controls are that
  layout's controls.

- **The safe-area insets are not a mobile-layout concern.** A notched phone in
  landscape is 844 px wide — above the breakpoint — so it renders the *desktop*
  layout while the home indicator is still there. Everything that reaches a
  screen edge therefore carries its own inset regardless of layout: the header
  and the content pane, the sheets, the tab bar, and both status bars. Only the
  content pane's *bottom* inset is variant-gated (`md:pb-safe`), because below
  the breakpoint the tab bar sits under it and already carries one. The helpers
  are registered with Tailwind's `@utility` rather than written as plain
  classes precisely so they can take that variant — and, as a side effect, so
  they land in the utilities layer instead of silently outranking Tailwind's
  own padding the way an unlayered rule does.

- **`dvh`, not `vh` or `svh`.** `dvh` tracks the viewport as the URL bar
  retracts and expands, which is what "fill the screen" means on a phone.
  `svh` would leave a permanent gap once the bar retracts. `#root` keeps
  `min-height: 100svh` — a *minimum* is the one place the small viewport is
  the right reference.

## Layout, above and below the breakpoint

| Region | ≥ 768 px (unchanged) | < 768 px |
| --- | --- | --- |
| Shell height | `h-screen` → `h-dvh` | `h-dvh` |
| Library nav | Pills, left of the header | Bottom tab bar, icon over label |
| Admin nav | Queue/Logs/Settings pills, right of the header | "More" menu in the header (admin only) |
| Profile | Avatar, header right | Avatar, header right |
| Notifications *(added 2026-08-28)* | Bell, header right of the admin pills | Bell, header right |
| Artist filter | 192 px sidebar, always open | `Artist: …` button opening a sheet |
| Rows | Table, 7–8 columns | Card list: cover, artist, title, meta |
| Sort | Column headers | `<select>` + direction toggle in the toolbar |
| Toolbar | One row | Search on its own row, controls below |
| Source filter | Dropdown anchored to its trigger | Sheet |
| Settings/Account rows | Three table columns | Stacked label → control → description |
| Status bars | `fixed bottom-0` | In the flow, above the tab bar |
| Modals | `w-96` | Full width less a gutter, buttons stacked |

Two rules are deliberately absent from this table because they are not keyed
on width at all: the 16 px form-control floor (`pointer: coarse`) and the
safe-area insets (`env()`, plus `md:pb-safe` where no tab bar carries the
bottom one). See the capability-versus-width decision above.

## Testing

`frontend/src/test/setup.ts` gains a `matchMedia` stub that reports no match,
so the default for the whole suite stays the desktop tree — the existing
files keep testing what they were written to test, unedited.

`frontend/src/test/mobileLayout.test.tsx` opts in per test by pointing the
stub at a viewport width, and covers what only exists below the breakpoint:
the bottom tab bar switches views, the admin overflow menu reaches Settings,
the artist sheet opens and applies a selection, the row list renders cards
rather than a `<table>`, and the sort control drives the same `sort`/`order`
the headers do.

Manual verification is a real device or an emulated one — layout at 390 ×
844 and 360 × 800, safe-area padding on a notched profile, and that focusing
the search input does not zoom.
