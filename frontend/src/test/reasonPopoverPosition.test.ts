import { describe, it, expect } from 'vitest'
import { placeReasonPopover, REASON_POPOVER_GAP, REASON_POPOVER_EDGE } from '../views/reasonPopoverPosition'

// jsdom measures every element as a zero-sized box at the origin, so the
// geometry cannot be asserted through a rendered popover. It lives here
// instead, where the rects are given rather than measured.
const VIEWPORT = { width: 1280, height: 800 }
const PANEL = { width: 256, height: 96 }

function anchorAt(left: number, top: number, size = 24) {
  return { left, top, right: left + size, width: size, height: size }
}

describe('placeReasonPopover', () => {
  it('sits to the left of the icon, one gap away', () => {
    const anchor = anchorAt(1200, 400)
    const { left } = placeReasonPopover(anchor, PANEL, VIEWPORT)
    expect(left + PANEL.width).toBe(anchor.left - REASON_POPOVER_GAP)
  })

  it('centres itself on the icon', () => {
    const anchor = anchorAt(1200, 400)
    const { top } = placeReasonPopover(anchor, PANEL, VIEWPORT)
    expect(top + PANEL.height / 2).toBe(anchor.top + anchor.height / 2)
  })

  it('flips to the icon\'s right when the left cannot hold it', () => {
    // A phone, where the row's actions are still at the right edge but the
    // panel is nearly as wide as the screen.
    const anchor = anchorAt(60, 300)
    const { left } = placeReasonPopover(anchor, PANEL, { width: 390, height: 844 })
    expect(left).toBe(anchor.right + REASON_POPOVER_GAP)
  })

  it('pulls the flipped panel back in rather than letting it overhang the right', () => {
    // A narrow screen where the icon is far enough left to flip, but the panel
    // would not fit between it and the right edge.
    const viewport = { width: 320, height: 640 }
    const { left } = placeReasonPopover(anchorAt(40, 300), PANEL, viewport)
    expect(left).toBe(viewport.width - PANEL.width - REASON_POPOVER_EDGE)
    expect(left + PANEL.width).toBeLessThanOrEqual(viewport.width)
  })

  it('never places a panel wider than the viewport off the left edge', () => {
    const { left } = placeReasonPopover(anchorAt(40, 300), PANEL, { width: 240, height: 640 })
    expect(left).toBe(REASON_POPOVER_EDGE)
  })

  it('does not run off the top for a row at the very top of the table', () => {
    const { top } = placeReasonPopover(anchorAt(1200, 4), PANEL, VIEWPORT)
    expect(top).toBe(REASON_POPOVER_EDGE)
  })

  it('does not run off the bottom for a row at the very bottom', () => {
    const { top } = placeReasonPopover(anchorAt(1200, 790), PANEL, VIEWPORT)
    expect(top).toBe(VIEWPORT.height - PANEL.height - REASON_POPOVER_EDGE)
  })

  it('prefers the top edge when the panel is taller than the viewport', () => {
    const tall = { width: 256, height: 900 }
    const { top } = placeReasonPopover(anchorAt(1200, 400), tall, VIEWPORT)
    expect(top).toBe(REASON_POPOVER_EDGE)
  })
})
