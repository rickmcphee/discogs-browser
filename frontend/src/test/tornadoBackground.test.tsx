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

  it('renders no fills, only strokes, so it composites as line art', () => {
    const { container } = render(<TornadoBackground />)
    const filledShapes = container.querySelectorAll('[fill]:not([fill="none"])')
    expect(filledShapes.length).toBe(0)
  })
})
