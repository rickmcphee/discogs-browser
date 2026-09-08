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

export interface Insets {
  top: number
  right: number
  bottom: number
  left: number
}

export interface Viewport {
  width: number
  height: number
  /** Safe-area insets, where the screen has them. `window.innerWidth` and
   *  `innerHeight` count the notch and the home indicator as usable screen,
   *  and the app's own layout does not (see `px-safe` in `index.css`), so the
   *  popover should not either. Absent means none, which is every desktop. */
  insets?: Insets
}

const NO_INSETS: Insets = { top: 0, right: 0, bottom: 0, left: 0 }

// Distance between the popover and the icon it belongs to, and the smallest
// margin it will leave against a viewport edge.
export const REASON_POPOVER_GAP = 8
export const REASON_POPOVER_EDGE = 8
// Below this, a shrunken panel is a sliver rather than something to read, and
// covering the icon is the lesser evil -- Escape and a press outside still
// dismiss it.
export const REASON_POPOVER_MIN_HEIGHT = 80
// How tall the popover will grow for its own sake before it starts scrolling,
// whatever room the viewport has. A glance at one sentence, not a page of it.
export const REASON_POPOVER_MAX_HEIGHT = 256

export interface Placement {
  top: number
  left: number
  /** The size to hold the panel to. Both always set: the panel depends on no
   *  CSS cap the placement cannot see, because a cap it cannot see is a cap it
   *  can contradict — bounds enforced against a width or height the panel does
   *  not actually have put it somewhere it does not actually fit. */
  maxHeight: number
  maxWidth: number
}

/** The widest the popover may render on this screen. Exported because the
 *  panel has to be held to it *before* its content height is measured -- the
 *  text reflows to this width, and a height read at a wider layout comes out
 *  short. `placeReasonPopover` applies the same cap, so passing it an
 *  already-capped width changes nothing. */
export function reasonPopoverMaxWidth(viewport: Viewport): number {
  const insets = viewport.insets ?? NO_INSETS
  return viewport.width - insets.left - insets.right - 2 * REASON_POPOVER_EDGE
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
  const insets = viewport.insets ?? NO_INSETS
  const firstLeft = REASON_POPOVER_EDGE + insets.left
  const firstTop = REASON_POPOVER_EDGE + insets.top

  // The size the panel will be held to, decided here rather than by a class,
  // so the bounds below are enforced against the panel that actually renders.
  // The safe screen, not the raw viewport: capping height against
  // `viewport.height` alone lets a panel through that is taller than the room
  // between the insets, and then every bound crosses its own start.
  const width = Math.min(panel.width, reasonPopoverMaxWidth(viewport))
  const height = Math.min(
    panel.height,
    REASON_POPOVER_MAX_HEIGHT,
    viewport.height - insets.top - insets.bottom - 2 * REASON_POPOVER_EDGE,
  )
  const lastLeft = viewport.width - insets.right - width - REASON_POPOVER_EDGE
  const lastTop = viewport.height - insets.bottom - height - REASON_POPOVER_EDGE

  // Both edges, for every candidate. The anchor is not necessarily on screen:
  // the table scrolls horizontally and the list vertically, so a row can be
  // scrolled past either edge while its popover is open, and a candidate that
  // clears the near edge can still land the panel off the far one.
  const fitsAcross = (left: number) => left >= firstLeft && left <= lastLeft
  const fitsDown = (top: number) => top >= firstTop && top <= lastTop

  const beside = clamp(anchor.top + anchor.height / 2 - height / 2, firstTop, lastTop)

  const toLeft = anchor.left - width - REASON_POPOVER_GAP
  if (fitsAcross(toLeft)) return { top: beside, left: toLeft, maxHeight: height, maxWidth: width }

  const toRight = anchor.right + REASON_POPOVER_GAP
  if (fitsAcross(toRight)) return { top: beside, left: toRight, maxHeight: height, maxWidth: width }

  // Stacked. Horizontally it starts at the icon's own left edge, which keeps
  // it near what it describes, and vertically it takes whichever side of the
  // icon can hold it -- covering the icon is the one thing it must not do,
  // since a second click there is how it closes.
  const left = clamp(anchor.left, firstLeft, lastLeft)
  const below = anchor.top + anchor.height + REASON_POPOVER_GAP
  if (fitsDown(below)) return { top: below, left, maxHeight: height, maxWidth: width }

  const above = anchor.top - height - REASON_POPOVER_GAP
  if (fitsDown(above)) return { top: above, left, maxHeight: height, maxWidth: width }

  // Taller than the space on either side of the icon. Nothing can keep the
  // panel whole, on screen and off the icon at once, and the icon is what
  // gives: clamping the panel into view would put it over the control that
  // closes it. So it takes the roomier side and is shortened to fit there,
  // scrolling what it cannot show.
  const roomBelow = viewport.height - insets.bottom - (anchor.top + anchor.height)
    - REASON_POPOVER_GAP - REASON_POPOVER_EDGE
  const roomAbove = anchor.top - REASON_POPOVER_GAP - firstTop
  const roomier = Math.max(roomBelow, roomAbove)
  if (roomier >= REASON_POPOVER_MIN_HEIGHT) {
    return roomBelow >= roomAbove
      ? { top: anchor.top + anchor.height + REASON_POPOVER_GAP, left, maxHeight: roomBelow, maxWidth: width }
      : { top: firstTop, left, maxHeight: roomAbove, maxWidth: width }
  }

  // An icon with almost no room on either side of it -- a viewport barely
  // taller than the icon itself. A readable panel over the icon beats an
  // unreadable one beside it; Escape and a press outside still dismiss it.
  return { top: beside, left, maxHeight: height, maxWidth: width }
}
