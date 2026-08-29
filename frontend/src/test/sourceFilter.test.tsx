import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import SourceFilter from '../components/SourceFilter'
import type { Crawler } from '../api/types'

const CRAWLERS: Crawler[] = [
  { id: 1, site_name: 'Amazon', module_path: '', crawler_type: 'release', enabled: true, last_run: null, base_url: null, genre: 'marketplace' },
  { id: 2, site_name: 'Epitaph', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: null, genre: 'punk' },
  { id: 3, site_name: 'Deathwish Inc', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: null, genre: 'punk' },
  { id: 4, site_name: 'Century Media', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: null, genre: 'metal' },
]

function renderFilter(overrides: Partial<{ crawlers: Crawler[]; hiddenCrawlerIds: number[]; onChange: (ids: number[]) => void; disabled: boolean }> = {}) {
  const onChange = overrides.onChange ?? vi.fn()
  render(
    <SourceFilter
      crawlers={overrides.crawlers ?? CRAWLERS}
      hiddenCrawlerIds={overrides.hiddenCrawlerIds ?? []}
      onChange={onChange}
      disabled={overrides.disabled}
    />
  )
  return onChange
}

function openDropdown() {
  fireEvent.click(screen.getByRole('button', { name: 'Source' }))
}

describe('SourceFilter', () => {
  it('renders a Source button and no dropdown until clicked', () => {
    renderFilter()
    expect(screen.getByRole('button', { name: 'Source' })).toBeInTheDocument()
    expect(screen.queryByText('By genre')).not.toBeInTheDocument()
  })

  it('exposes aria-expanded reflecting the dropdown open state', () => {
    renderFilter()
    const button = screen.getByRole('button', { name: 'Source' })
    expect(button).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
  })

  it('disables the Source button and refuses to open when disabled', () => {
    renderFilter({ disabled: true })
    const button = screen.getByRole('button', { name: 'Source' })
    expect(button).toBeDisabled()
    fireEvent.click(button)
    expect(screen.queryByText('By genre')).not.toBeInTheDocument()
  })

  it('opens the dropdown showing every genre and every store grouped under its genre', () => {
    renderFilter()
    openDropdown()
    expect(screen.getByText('By genre')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Marketplace' })).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Punk' })).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Metal' })).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Rock' })).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Pop' })).toBeInTheDocument()
    expect(screen.getByText('Amazon')).toBeInTheDocument()
    expect(screen.getByText('Epitaph')).toBeInTheDocument()
    expect(screen.getByText('Century Media')).toBeInTheDocument()
  })

  it('checks a store checkbox when it is not in hiddenCrawlerIds', () => {
    renderFilter({ hiddenCrawlerIds: [] })
    openDropdown()
    expect(screen.getByRole('checkbox', { name: 'Epitaph' })).toBeChecked()
  })

  it('unchecks a store checkbox when it is in hiddenCrawlerIds', () => {
    renderFilter({ hiddenCrawlerIds: [2] })
    openDropdown()
    expect(screen.getByRole('checkbox', { name: 'Epitaph' })).not.toBeChecked()
  })

  it('clicking a visible store checkbox calls onChange adding it to the hidden set', () => {
    const onChange = renderFilter({ hiddenCrawlerIds: [] })
    openDropdown()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Epitaph' }))
    expect(onChange).toHaveBeenCalledWith([2])
  })

  it('clicking a hidden store checkbox calls onChange removing it from the hidden set', () => {
    const onChange = renderFilter({ hiddenCrawlerIds: [2, 3] })
    openDropdown()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Epitaph' }))
    expect(onChange).toHaveBeenCalledWith([3])
  })

  it('checks the genre checkbox when every store in that genre is visible', () => {
    renderFilter({ hiddenCrawlerIds: [] })
    openDropdown()
    expect(screen.getByRole('checkbox', { name: 'Punk' })).toBeChecked()
  })

  it('unchecks the genre checkbox when every store in that genre is hidden', () => {
    renderFilter({ hiddenCrawlerIds: [2, 3] })
    openDropdown()
    expect(screen.getByRole('checkbox', { name: 'Punk' })).not.toBeChecked()
  })

  it('marks the genre checkbox indeterminate when some but not all stores in that genre are hidden', () => {
    renderFilter({ hiddenCrawlerIds: [2] })
    openDropdown()
    const checkbox = screen.getByRole('checkbox', { name: 'Punk' }) as HTMLInputElement
    expect(checkbox.indeterminate).toBe(true)
  })

  it('clicking a fully-visible genre checkbox hides every store in that genre', () => {
    const onChange = renderFilter({ hiddenCrawlerIds: [] })
    openDropdown()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Punk' }))
    expect(onChange).toHaveBeenCalledWith(expect.arrayContaining([2, 3]))
    expect((onChange as any).mock.calls[0][0].length).toBe(2)
  })

  it('clicking a mixed genre checkbox shows every store in that genre', () => {
    const onChange = renderFilter({ hiddenCrawlerIds: [2, 4] })
    openDropdown()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Punk' }))
    expect(onChange).toHaveBeenCalledWith([4])
  })

  it('clicking Show all calls onChange with an empty array', () => {
    const onChange = renderFilter({ hiddenCrawlerIds: [2, 3, 4] })
    openDropdown()
    fireEvent.click(screen.getByText('Show all'))
    expect(onChange).toHaveBeenCalledWith([])
  })

  it('clicking Clear all calls onChange with every crawler id', () => {
    const onChange = renderFilter({ hiddenCrawlerIds: [] })
    openDropdown()
    fireEvent.click(screen.getByText('Clear all'))
    expect(onChange).toHaveBeenCalledWith(expect.arrayContaining([1, 2, 3, 4]))
    expect((onChange as any).mock.calls[0][0].length).toBe(4)
  })

  it('disables Clear all and does not call onChange while crawlers have not loaded yet', () => {
    const onChange = renderFilter({ crawlers: [], hiddenCrawlerIds: [2, 3, 4] })
    openDropdown()
    const clearAll = screen.getByText('Clear all')
    expect(clearAll).toBeDisabled()
    fireEvent.click(clearAll)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('closes the dropdown when clicking outside it', () => {
    render(
      <div>
        <div data-testid="outside">outside</div>
        <SourceFilter crawlers={CRAWLERS} hiddenCrawlerIds={[]} onChange={vi.fn()} />
      </div>
    )
    openDropdown()
    expect(screen.getByText('By genre')).toBeInTheDocument()
    fireEvent.mouseDown(screen.getByTestId('outside'))
    expect(screen.queryByText('By genre')).not.toBeInTheDocument()
  })
})
