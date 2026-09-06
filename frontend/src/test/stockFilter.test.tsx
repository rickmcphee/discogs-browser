import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import StockFilter from '../components/StockFilter'

const defaultMatchMedia = window.matchMedia

// The hook only ever asks a max-width question; answering it "yes" is all it
// takes to put a render on the phone side of the breakpoint.
function stubMobile() {
  window.matchMedia = ((query: string) => ({
    matches: query.startsWith('(max-width'),
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }) as unknown as MediaQueryList) as typeof window.matchMedia
}

afterEach(() => {
  window.matchMedia = defaultMatchMedia
})

function renderFilter(overrides: Partial<{
  filter: string
  onFilterChange: (value: string) => void
  recommendedAvailable: boolean
  cheapest: boolean
  onCheapestChange: (value: boolean) => void
}> = {}) {
  const props = {
    filter: 'all',
    onFilterChange: vi.fn(),
    recommendedAvailable: false,
    cheapest: false,
    onCheapestChange: vi.fn(),
    ...overrides,
  }
  render(<StockFilter {...props} />)
  return props
}

const trigger = () => screen.getByRole('button', { name: 'Filter' })

describe('StockFilter', () => {
  it('renders a trigger called Filter, and no panel until clicked', () => {
    renderFilter()
    expect(trigger()).toHaveTextContent(/^Filter$/)
    expect(trigger().getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('radio')).toBeNull()
  })

  it('opens an anchored panel listing every filter in order, library filters last, with Cheapest beneath them', () => {
    renderFilter()
    fireEvent.click(trigger())
    expect(trigger().getAttribute('aria-expanded')).toBe('true')
    expect(screen.getAllByRole('radio').map((r) => (r as HTMLInputElement).value))
      .toEqual(['all', 'recommended', 'saved', 'overlapped', 'collection', 'wantlist'])
    expect((screen.getByRole('radio', { name: 'All' }) as HTMLInputElement).checked).toBe(true)
    expect(screen.getByRole('checkbox', { name: 'Cheapest' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('checks the current library filter in the panel like any other', () => {
    renderFilter({ filter: 'wantlist', cheapest: true })
    fireEvent.click(trigger())
    expect((screen.getByRole('radio', { name: 'Wantlist' }) as HTMLInputElement).checked).toBe(true)
    expect((screen.getByRole('checkbox', { name: 'Cheapest' }) as HTMLInputElement).checked).toBe(true)
  })

  it('disables Recommended until recommendations are available', () => {
    renderFilter()
    fireEvent.click(trigger())
    expect((screen.getByRole('radio', { name: 'Recommended' }) as HTMLInputElement).disabled).toBe(true)
  })

  it('enables Recommended when recommendations are available', () => {
    renderFilter({ recommendedAvailable: true })
    fireEvent.click(trigger())
    expect((screen.getByRole('radio', { name: 'Recommended' }) as HTMLInputElement).disabled).toBe(false)
  })

  it('reports a chosen filter and keeps the panel open for a second choice', () => {
    const { onFilterChange } = renderFilter()
    fireEvent.click(trigger())
    fireEvent.click(screen.getByRole('radio', { name: 'Saved' }))
    expect(onFilterChange).toHaveBeenCalledWith('saved')
    expect(screen.getByRole('checkbox', { name: 'Cheapest' })).toBeInTheDocument()
  })

  it('reports the Cheapest toggle', () => {
    const { onCheapestChange } = renderFilter()
    fireEvent.click(trigger())
    fireEvent.click(screen.getByRole('checkbox', { name: 'Cheapest' }))
    expect(onCheapestChange).toHaveBeenCalledWith(true)
  })

  it('keeps the trigger reading Filter, unlit, whatever the state', () => {
    renderFilter({ filter: 'saved', cheapest: true })
    expect(trigger()).toHaveTextContent(/^Filter$/)
    expect(trigger()).not.toHaveClass('bg-white')
  })

  it('lights the trigger only while the panel is open', () => {
    renderFilter({ cheapest: true })
    expect(trigger()).not.toHaveClass('bg-white')
    fireEvent.click(trigger())
    expect(trigger()).toHaveClass('bg-white')
    fireEvent.click(trigger())
    expect(trigger()).not.toHaveClass('bg-white')
  })

  it('does not light the trigger in the default state', () => {
    renderFilter()
    expect(trigger()).not.toHaveClass('bg-white')
  })

  it('closes the desktop panel on an outside click and on a second click of the trigger', () => {
    renderFilter()
    fireEvent.click(trigger())
    expect(screen.queryByRole('radio', { name: 'All' })).toBeInTheDocument()
    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('radio', { name: 'All' })).toBeNull()
    fireEvent.click(trigger())
    fireEvent.click(trigger())
    expect(screen.queryByRole('radio', { name: 'All' })).toBeNull()
  })

  it('renders the panel as a sheet on a phone', () => {
    stubMobile()
    renderFilter()
    fireEvent.click(trigger())
    const sheet = screen.getByRole('dialog', { name: 'Filter' })
    expect(sheet).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Saved' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
