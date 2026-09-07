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

  it('flips to the icon\'s right when the left cannot hold it and the right can', () => {
    const anchor = anchorAt(60, 300)
    const { left } = placeReasonPopover(anchor, PANEL, { width: 640, height: 844 })
    expect(left).toBe(anchor.right + REASON_POPOVER_GAP)
  })

  it('stacks below rather than flipping onto the icon it has to be clicked to close', () => {
    // A phone: the icon is near the card's right-hand actions and 256px fits
    // on neither side of it. Flipping right and clamping back would put the
    // panel over its own toggle, which is the one place it must not go.
    const viewport = { width: 375, height: 812 }
    const anchor = anchorAt(264, 300, 44)
    const { top, left } = placeReasonPopover(anchor, PANEL, viewport)
    expect(top).toBe(anchor.top + anchor.height + REASON_POPOVER_GAP)
    expect(left).toBe(viewport.width - PANEL.width - REASON_POPOVER_EDGE)
    // Clear of the icon's own band, so the second click still reaches it.
    expect(top).toBeGreaterThanOrEqual(anchor.top + anchor.height)
  })

  it('stacks above when there is no room below', () => {
    const viewport = { width: 375, height: 812 }
    const anchor = anchorAt(264, 700, 44)
    const { top } = placeReasonPopover(anchor, PANEL, viewport)
    expect(top + PANEL.height).toBe(anchor.top - REASON_POPOVER_GAP)
    expect(top + PANEL.height).toBeLessThanOrEqual(anchor.top)
  })

  it('still sits beside an icon that has room on its left, however narrow the screen', () => {
    const viewport = { width: 375, height: 812 }
    const anchor = anchorAt(320, 300, 44)
    const { top, left } = placeReasonPopover(anchor, PANEL, viewport)
    expect(left + PANEL.width).toBe(anchor.left - REASON_POPOVER_GAP)
    expect(top + PANEL.height / 2).toBe(anchor.top + anchor.height / 2)
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

  // An anchor entirely off screen closes the popover before it reaches this
  // function (see StockBrowser's placement effect), but one straddling an edge
  // does not -- and there, room beside the anchor is still not room on screen.
  it('stays on screen for an icon straddling the right edge', () => {
    const { left } = placeReasonPopover(anchorAt(VIEWPORT.width - 10, 400), PANEL, VIEWPORT)
    expect(left).toBeGreaterThanOrEqual(REASON_POPOVER_EDGE)
    expect(left + PANEL.width).toBeLessThanOrEqual(VIEWPORT.width)
  })

  it('stays on screen for an icon straddling the left edge', () => {
    const { left } = placeReasonPopover(anchorAt(-10, 400), PANEL, VIEWPORT)
    expect(left).toBeGreaterThanOrEqual(REASON_POPOVER_EDGE)
    expect(left + PANEL.width).toBeLessThanOrEqual(VIEWPORT.width)
  })

  it('stays on screen for a row straddling the top edge', () => {
    const { top } = placeReasonPopover(anchorAt(1200, -20), PANEL, VIEWPORT)
    expect(top).toBeGreaterThanOrEqual(REASON_POPOVER_EDGE)
    expect(top + PANEL.height).toBeLessThanOrEqual(VIEWPORT.height)
  })

  it('stays on screen for a row straddling the bottom edge', () => {
    const { top } = placeReasonPopover(anchorAt(1200, VIEWPORT.height - 10), PANEL, VIEWPORT)
    expect(top).toBeGreaterThanOrEqual(REASON_POPOVER_EDGE)
    expect(top + PANEL.height).toBeLessThanOrEqual(VIEWPORT.height)
  })

  it('prefers the top edge when the panel is taller than the viewport', () => {
    const tall = { width: 256, height: 900 }
    const { top } = placeReasonPopover(anchorAt(1200, 400), tall, VIEWPORT)
    expect(top).toBe(REASON_POPOVER_EDGE)
  })
})
