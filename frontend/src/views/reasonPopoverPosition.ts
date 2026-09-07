export interface Rect {
  top: number
  left: number
  right: number
  width: number
  height: number
}

export interface Size {
  width: number
  height: number
}

export interface Viewport {
  width: number
  height: number
}

// Distance between the popover and the icon it belongs to, and the smallest
// margin it will leave against a viewport edge.
export const REASON_POPOVER_GAP = 8
export const REASON_POPOVER_EDGE = 8

function clamp(value: number, lowest: number, highest: number): number {
  // Lowest wins a crossed range: a panel too big for the space is pinned to
  // the top-left edge rather than pushed off the opposite one.
  return Math.max(lowest, Math.min(value, highest))
}

/** Viewport coordinates for the reason popover, placed to the left of its icon.
 *
 * Left, because that is where the space is: the icon sits in the row's
 * right-hand action group, so the row's own width is free on that side. It
 * flips to the icon's right only when the left cannot hold it *and* the right
 * can -- a flip that has to be clamped back over the icon would bury the
 * control that closes it. When neither side fits, it stacks below the icon, or
 * above when below is short. Fixed coordinates rather than a position inside
 * the table, which is `overflow-auto` and would clip a row at its top edge
 * with no scroll to recover it. */
export function placeReasonPopover(anchor: Rect, panel: Size, viewport: Viewport): { top: number; left: number } {
  const lastLeft = viewport.width - panel.width - REASON_POPOVER_EDGE
  const lastTop = viewport.height - panel.height - REASON_POPOVER_EDGE

  // Both edges, for every candidate. The anchor is not necessarily on screen:
  // the table scrolls horizontally and the list vertically, so a row can be
  // scrolled past either edge while its popover is open, and a candidate that
  // clears the near edge can still land the panel off the far one.
  const fitsAcross = (left: number) => left >= REASON_POPOVER_EDGE && left <= lastLeft
  const fitsDown = (top: number) => top >= REASON_POPOVER_EDGE && top <= lastTop

  const beside = clamp(anchor.top + anchor.height / 2 - panel.height / 2, REASON_POPOVER_EDGE, lastTop)

  const toLeft = anchor.left - panel.width - REASON_POPOVER_GAP
  if (fitsAcross(toLeft)) return { top: beside, left: toLeft }

  const toRight = anchor.right + REASON_POPOVER_GAP
  if (fitsAcross(toRight)) return { top: beside, left: toRight }

  // Stacked. Horizontally it starts at the icon's own left edge, which keeps
  // it near what it describes, and vertically it takes whichever side of the
  // icon can hold it -- covering the icon is the one thing it must not do,
  // since a second click there is how it closes.
  const left = clamp(anchor.left, REASON_POPOVER_EDGE, lastLeft)
  const below = anchor.top + anchor.height + REASON_POPOVER_GAP
  if (fitsDown(below)) return { top: below, left }

  const above = anchor.top - panel.height - REASON_POPOVER_GAP
  if (fitsDown(above)) return { top: above, left }

  // Taller than the space on either side of the icon, or an icon that is not
  // on screen at all: nothing can both avoid the overlap and stay in view, so
  // fall back to the clamped reading position and let Escape or a press
  // outside dismiss it.
  return { top: beside, left }
}
