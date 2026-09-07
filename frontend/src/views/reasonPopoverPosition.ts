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

/** Viewport coordinates for the reason popover, placed to the left of its icon.
 *
 * Left, because that is where the space is: the icon sits in the row's
 * right-hand action group, so the row's own width is free on that side. The
 * popover flips to the icon's right only when the left cannot hold it, and is
 * clamped to the viewport on both axes either way -- it is positioned fixed
 * rather than inside the scrolling table precisely so a row at the very top or
 * bottom edge is not clipped by an ancestor's overflow. */
export function placeReasonPopover(anchor: Rect, panel: Size, viewport: Viewport): { top: number; left: number } {
  let left = anchor.left - panel.width - REASON_POPOVER_GAP
  if (left < REASON_POPOVER_EDGE) {
    left = Math.min(anchor.right + REASON_POPOVER_GAP, viewport.width - panel.width - REASON_POPOVER_EDGE)
  }
  // A panel wider than the viewport can leave the flip below the edge margin
  // too, so the clamp is applied to whichever side won.
  left = Math.max(REASON_POPOVER_EDGE, left)

  const centred = anchor.top + anchor.height / 2 - panel.height / 2
  const lowest = viewport.height - panel.height - REASON_POPOVER_EDGE
  const top = Math.max(REASON_POPOVER_EDGE, Math.min(centred, lowest))

  return { top, left }
}
