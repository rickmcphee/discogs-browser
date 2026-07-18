import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import Avatar from '../components/Avatar'

describe('Avatar', () => {
  it('renders the default glyph when there is no photo', () => {
    render(<Avatar version={0} size="sm" />)
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('renders the uploaded photo when a version is set', () => {
    render(<Avatar version={123} size="sm" />)
    const img = screen.getByRole('img') as HTMLImageElement
    expect(img.src).toContain('v=123')
  })

  it('falls back to the glyph if the photo fails to load', () => {
    render(<Avatar version={123} size="sm" />)
    fireEvent.error(screen.getByRole('img'))
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })
})
