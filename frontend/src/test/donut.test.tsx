import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import Donut from '../components/Donut'

const R = 60
const C = 2 * Math.PI * R

function dashOf(el: Element): number {
  return Number(el.getAttribute('stroke-dasharray')!.split(' ')[0])
}

function arcs(): Element[] {
  const donut = screen.getByRole('img', { name: 'test ring' })
  // The first circle is the track, which carries no dasharray.
  return Array.from(donut.querySelectorAll('circle')).filter((c) => c.getAttribute('stroke-dasharray'))
}

function renderDonut(segments: { key: string; value: number }[]) {
  render(
    <Donut
      segments={segments.map((s) => ({ ...s, color: '#3987e5', title: s.key }))}
      centreValue="x"
      centreLabel="y"
      ariaLabel="test ring"
    />
  )
}

describe('Donut', () => {
  it('never draws a segment wider than its share of the circumference', () => {
    // 1 of 5,000 is allocated 0.08 of the ring. Drawn at a bare 0.5 floor it
    // would paint over the wedge that follows and overstate the sliver.
    renderDonut([{ key: 'sliver', value: 1 }, { key: 'rest', value: 4999 }])
    const [sliver] = arcs()
    const allocated = (1 / 5000) * C
    expect(dashOf(sliver)).toBeLessThanOrEqual(allocated)
  })

  it('keeps the 2px gap on segments large enough to afford one', () => {
    renderDonut([{ key: 'a', value: 1 }, { key: 'b', value: 1 }])
    const allocated = C / 2
    expect(dashOf(arcs()[0])).toBeCloseTo(allocated - 2, 5)
  })

  it('gives a whole-ring segment the full circumference less its gap', () => {
    renderDonut([{ key: 'only', value: 7 }])
    expect(dashOf(arcs()[0])).toBeCloseTo(C - 2, 5)
  })
})
