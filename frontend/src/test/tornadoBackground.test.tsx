import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import TornadoBackground from '../components/TornadoBackground'

describe('TornadoBackground', () => {
  it('renders a hidden decorative svg with no accessible role', () => {
    const { container } = render(<TornadoBackground />)
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    expect(svg).toHaveAttribute('aria-hidden', 'true')
  })

  it('renders line art only: fill/stroke set once on the svg root, never overridden by a shape', () => {
    const { container } = render(<TornadoBackground />)
    const svg = container.querySelector('svg')
    expect(svg).toHaveAttribute('fill', 'none')
    expect(svg).toHaveAttribute('stroke', 'currentColor')

    const shapes = container.querySelectorAll('circle, ellipse, path, rect, polygon')
    expect(shapes.length).toBeGreaterThan(0)
    shapes.forEach((shape) => {
      expect(shape).not.toHaveAttribute('fill')
      expect(shape).not.toHaveAttribute('stroke')
    })
  })
})
