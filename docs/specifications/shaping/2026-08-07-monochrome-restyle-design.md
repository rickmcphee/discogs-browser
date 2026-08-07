# Monochrome restyle design

Date: 2026-08-07
Branch: `monochrome-restyle`

## Problem

The frontend's accent color is Tailwind `indigo-*`, used for primary/active
buttons, focus rings, spinners, the avatar ring, and Settings toggle switches.
Reference: kimi.com (https://www.kimi.com/en) uses rounded, low-contrast
black/gray/white controls instead of a saturated brand color. We want that
look here.

## Scope

Full theme overhaul: every `indigo-*` usage across the frontend becomes
grayscale, and interactive/container corner radii increase. This touches
every view file that currently references `indigo-*`:

- `src/App.tsx`
- `src/views/Account.tsx`
- `src/views/DebugView.tsx`
- `src/views/InviteCodeScreen.tsx`
- `src/views/LogViewer.tsx`
- `src/views/LoginScreen.tsx`
- `src/views/RecordBrowser.tsx`
- `src/views/Settings.tsx`
- `src/views/StockBrowser.tsx`

Out of scope: `red`/`green`/`yellow` status colors (errors, in-stock
indicators, log severity) are functional, not brand color, and are not
touched. Layout, typography, spacing, and animation timing are unchanged.
`src/index.css` / `src/App.css` are leftover Vite template boilerplate not
referenced by any component and are not touched by this change.

## Palette mapping

| Role | Current | New |
|---|---|---|
| Primary/active (active nav tab, primary modal button) | `bg-indigo-600` + white text | `bg-white text-gray-950`, `hover:bg-gray-200` |
| Secondary action | `bg-gray-700 hover:bg-gray-600` | unchanged |
| Inactive nav / ghost text button | `text-gray-400 hover:text-white` | unchanged, add `hover:bg-gray-800` |
| Accent/info text (e.g. crawl-banner site name, modal subtext) | `text-indigo-300`/`text-indigo-400` | `text-gray-300`/`text-gray-400` |
| Focus ring / avatar ring / active-tab ring | `ring-indigo-500` | `ring-white/70` |
| Spinners | `border-indigo-500 border-t-transparent` | `border-white/70 border-t-transparent` |
| Settings toggle switches | `bg-indigo-600`/`700`/`800` | `bg-white` (on) / `bg-gray-700` (off), dark thumb |

## Shape mapping

- Buttons (nav pills, dismiss buttons, modal action buttons): `rounded` → `rounded-full`.
- Modals, cards, inputs, dropdowns: `rounded-lg`/`rounded` → `rounded-xl`.
- Avatar: already `rounded-full`, unchanged.

## Implementation approach

Nav/action buttons currently duplicate the same conditional className string
across 9 files (15+ call sites). Editing each by hand risks inconsistent
adoption of the new style. Add `frontend/src/styles/buttons.ts` exporting
plain functions returning class strings:

- `navButtonClass(isActive: boolean): string`
- `primaryButtonClass(): string`
- `secondaryButtonClass(): string`
- `dismissButtonClass(): string`

These are plain functions, not a wrapping `<Button>` component, because
call sites vary too much (icons, subtext `<span>`s, conditional disabled
states, non-button elements like the avatar toggle) to share one JSX
signature cleanly. Each existing inline className is replaced with a call
to the matching helper.

## Testing

Two existing tests assert on the current indigo classes and must be updated
to assert on the new grayscale classes instead:

- `frontend/src/test/accountNav.test.tsx:62` — asserts `ring-2 ring-indigo-500`, becomes `ring-2 ring-white/70`.
- `frontend/src/test/inStockTab.test.tsx:93` — asserts `bg-indigo-600`, becomes `bg-white`.

No other test files assert on color or radius classes (verified via grep for
`indigo`/`rounded` across `frontend/src/test/`).

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo. This change is a pure visual restyle of existing UI: it
adds no new trigger, input, output, or external call, and does not change
the stack, golden commands, or CI/CD. `README.md` has no color or screenshot
references to update. No agent-facing documentation changes are needed.

## Amendment (2026-08-07, post-implementation)

- The palette table's `ring-indigo-500 → ring-white/70` and
  `border-indigo-500 border-t-transparent → border-white/70 border-t-transparent`
  rows shipped without the `/70` opacity — actual values are `ring-white` and
  `border-white` (solid, not translucent). This was a deliberate
  simplification made during implementation, not an oversight.
- The shape mapping's "Modals, cards, inputs, dropdowns → rounded-xl" shipped
  only for modals/cards (App.tsx's two modals, LoginScreen's card,
  InviteCodeScreen's form, DebugView's session panel) — plain
  `<input>`/`<select>` elements across the app were intentionally left at the
  smaller `rounded`, since bumping every text input's corner radius was
  outside what this restyle's plan scoped (the plan's per-file tasks never
  touched input radius, only input focus-border color).
- The palette table's "Settings toggle switches" row was inaccurate when
  written — Settings.tsx's `toggleButtonClass` (Visible/Hidden,
  Enabled/Disabled chips) is green/gray, never indigo; it only needed a
  radius bump, which shipped. The actual indigo-to-white toggle-style switch
  is Account.tsx's admin/user role switch, which shipped as `bg-gray-800`
  (admin) / `bg-gray-600` (user) — two grays, not white/gray as the table
  stated.
