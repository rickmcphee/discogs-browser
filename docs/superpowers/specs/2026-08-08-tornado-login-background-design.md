# Tornado line-art background for the login screen

## Purpose

`LoginScreen.tsx` is currently a bare centered card on a solid background. Add a full-viewport line-drawing background — a tornado funnel sweeping vinyl records into the air — behind the sign-in card, as a first piece of visual identity ahead of a future rebrand ("Track Tempest"). Scope is limited to `LoginScreen.tsx`; no app rename, no other views.

## Design

- New component `frontend/src/components/TornadoBackground.tsx`: a hand-authored SVG (no external image asset, no new dependency). A funnel of converging spiral lines rising from the bottom of the viewport, with ~7 line-art records (circle + concentric groove rings + center hole) swept through and around it at varying sizes/rotations, some flung out near the top. Correction: shipped as a stack of discrete concentric elliptical hoops (no connecting spiral lines between them), not a continuous spiral.
- Single-color line art: `stroke="currentColor"`, no fill, `fill="none"`. Correction: `LoginScreen.tsx` and `InviteCodeScreen.tsx` both hardcode a fixed dark Tailwind palette (`bg-gray-950`/`bg-gray-900`/`border-gray-700`) regardless of `prefers-color-scheme` — they don't use the adaptive `--text`/`--bg` CSS vars from `index.css`. So the background uses a low-opacity fixed Tailwind gray consistent with that existing fixed-dark pattern, not a theme variable. Correction: shipped as `text-gray-500` at `opacity-[0.4]`, not `text-gray-700`/`opacity-[0.15]` — the original values composited to a ~1.06:1 contrast ratio against `bg-gray-950`, effectively invisible; the whole-branch review caught it and the fix raised it to ~1.57:1.
- `LoginScreen.tsx`: wrapping div becomes `relative overflow-hidden`. `<TornadoBackground />` renders first, absolutely positioned (`absolute inset-0 w-full h-full`), `pointer-events-none`, `aria-hidden="true"`. The existing card gets `relative z-10` so it stays on top and legible.
- Static only — no animation, no `prefers-reduced-motion` handling needed since there's no motion.

## Non-goals

- No app rename (title, storage keys, headings) — separate future task.
- No changes to other views, backend, or docs beyond this spec.

## Testing

- Run existing `frontend/src/test/loginScreen.test.tsx` — background is `aria-hidden` and adds no text/roles, so it should be unaffected. If it breaks, fix the test rather than removing the a11y attributes.
- Manual: visually confirm the card stays readable over the background (the screen is always dark regardless of `prefers-color-scheme`, so only one appearance to check).
