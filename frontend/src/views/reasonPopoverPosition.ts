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
// Below this, a shrunken panel is a sliver rather than something to read, and
// covering the icon is the lesser evil -- Escape and a press outside still
// dismiss it.
export const REASON_POPOVER_MIN_HEIGHT = 80

export interface Placement {
  top: number
  left: number
  /** Set only where the panel has to be shortened to clear the icon; the
   *  panel's own CSS cap applies when it is absent. */
  maxHeight?: number
}

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
 * above when below is short, and where it fits neither whole it takes the
 * roomier side and reports the `maxHeight` that will hold it there. Fixed
 * coordinates rather than a position inside the table, which is
 * `overflow-auto` and would clip a row at its top edge with no scroll to
 * recover it. */
export function placeReasonPopover(anchor: Rect, panel: Size, viewport: Viewport): Placement {
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

  // Taller than the space on either side of the icon. Nothing can keep the
  // panel whole, on screen and off the icon at once, and the icon is what
  // gives: clamping the panel into view would put it over the control that
  // closes it. So it takes the roomier side and is shortened to fit there,
  // scrolling what it cannot show.
  const roomBelow = viewport.height - (anchor.top + anchor.height) - REASON_POPOVER_GAP - REASON_POPOVER_EDGE
  const roomAbove = anchor.top - REASON_POPOVER_GAP - REASON_POPOVER_EDGE
  const roomier = Math.max(roomBelow, roomAbove)
  if (roomier >= REASON_POPOVER_MIN_HEIGHT) {
    return roomBelow >= roomAbove
      ? { top: anchor.top + anchor.height + REASON_POPOVER_GAP, left, maxHeight: roomBelow }
      : { top: REASON_POPOVER_EDGE, left, maxHeight: roomAbove }
  }

  // An icon with almost no room on either side of it -- a viewport barely
  // taller than the icon itself. A readable panel over the icon beats an
  // unreadable one beside it; Escape and a press outside still dismiss it.
  return { top: beside, left }
}
