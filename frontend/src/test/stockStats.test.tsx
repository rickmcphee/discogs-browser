import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import StockStats from '../components/StockStats'
import { toSlices } from '../components/stockSlices'
import type { StockSourceCount } from '../api/types'

const getStockStats = vi.fn()

vi.mock('../api/client', () => ({
  getStockStats: (...args: unknown[]) => getStockStats(...args),
}))

const SOURCES: StockSourceCount[] = [
  { crawler_id: 1, site_name: 'Nuclear Blast', count: 300 },
  { crawler_id: 2, site_name: 'Epitaph', count: 150 },
  { crawler_id: 3, site_name: 'Deathwish Inc', count: 50 },
]

function renderStats(props: Partial<React.ComponentProps<typeof StockStats>> = {}) {
  return render(<StockStats hiddenCrawlerIds={[]} {...props} />)
}

function openPanel() {
  fireEvent.click(screen.getByRole('button', { name: 'Stats' }))
}

beforeEach(() => {
  getStockStats.mockReset()
  getStockStats.mockResolvedValue({ total: 500, sources: SOURCES })
})

describe('StockStats', () => {
  it('renders a Stats button and fetches nothing until it is opened', () => {
    renderStats()
    expect(screen.getByRole('button', { name: 'Stats' })).toBeInTheDocument()
    expect(screen.queryByText('Items by source')).not.toBeInTheDocument()
    expect(getStockStats).not.toHaveBeenCalled()
  })

  it('exposes aria-expanded reflecting the panel state', () => {
    renderStats()
    const button = screen.getByRole('button', { name: 'Stats' })
    expect(button).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
  })

  it('refuses to open, and fetches nothing, while disabled', () => {
    renderStats({ disabled: true })
    const button = screen.getByRole('button', { name: 'Stats' })
    expect(button).toBeDisabled()
    fireEvent.click(button)
    expect(screen.queryByText('Items by source')).not.toBeInTheDocument()
    expect(getStockStats).not.toHaveBeenCalled()
  })

  it('draws the ring with the total at its centre', async () => {
    renderStats()
    openPanel()
    const donut = await screen.findByRole('img', { name: /items by source/i })
    expect(donut).toHaveTextContent('500')
    expect(donut).toHaveTextContent('items')
    // One arc per source, over the track circle.
    expect(donut.querySelectorAll('circle').length).toBe(SOURCES.length + 1)
  })

  it('lists every source with its own count and share of the total', async () => {
    renderStats()
    openPanel()
    await screen.findByRole('img', { name: /items by source/i })
    for (const [name, count, pct] of [
      ['Nuclear Blast', '300', '60%'],
      ['Epitaph', '150', '30%'],
      ['Deathwish Inc', '50', '10%'],
    ]) {
      const row = screen.getByText(name).closest('li')!
      expect(row).toHaveTextContent(count)
      expect(row).toHaveTextContent(pct)
    }
  })

  it('passes the browser’s current filters through to the request', async () => {
    renderStats({
      search: 'zombie', artist: 'Rob Zombie', saved: true, hiddenCrawlerIds: [7, 9],
    })
    openPanel()
    await waitFor(() => expect(getStockStats).toHaveBeenCalled())
    expect(getStockStats).toHaveBeenCalledWith(expect.objectContaining({
      search: 'zombie', artist: 'Rob Zombie', saved: true, hiddenCrawlerIds: [7, 9],
    }))
  })

  it('refetches when a filter changes while the panel is open', async () => {
    const { rerender } = renderStats({ search: 'zombie' })
    openPanel()
    await waitFor(() => expect(getStockStats).toHaveBeenCalledTimes(1))
    rerender(<StockStats hiddenCrawlerIds={[]} search="rancid" />)
    await waitFor(() => expect(getStockStats).toHaveBeenCalledTimes(2))
    expect(getStockStats).toHaveBeenLastCalledWith(expect.objectContaining({ search: 'rancid' }))
  })

  it('drops the old breakdown the moment a filter changes, not when the new one lands', async () => {
    const { rerender } = renderStats({ search: 'zombie' })
    openPanel()
    await screen.findByRole('img', { name: /items by source/i })

    let resolve: (v: unknown) => void = () => {}
    getStockStats.mockReturnValue(new Promise((r) => { resolve = r }))
    rerender(<StockStats hiddenCrawlerIds={[]} search="rancid" />)
    // The request for 'rancid' is still in flight; the 'zombie' numbers must
    // already be gone rather than sitting next to a toolbar count that moved.
    expect(screen.queryByRole('img', { name: /items by source/i })).toBeNull()
    expect(screen.queryByText('Nuclear Blast')).toBeNull()

    resolve({ total: 4, sources: [{ crawler_id: 8, site_name: 'Rancid Records', count: 4 }] })
    expect(await screen.findByText('Rancid Records')).toBeInTheDocument()
  })

  it('keeps the breakdown on screen across a refreshKey tick, so a sync does not strobe it', async () => {
    const { rerender } = renderStats({ refreshKey: 0 })
    openPanel()
    await screen.findByRole('img', { name: /items by source/i })

    getStockStats.mockReturnValue(new Promise(() => {}))
    rerender(<StockStats hiddenCrawlerIds={[]} refreshKey={1} />)
    // Same view, only newer data pending: the numbers are seconds stale, not
    // describing something else, so they stay put until the response lands.
    expect(screen.getByRole('img', { name: /items by source/i })).toBeInTheDocument()
    expect(screen.getByText('Nuclear Blast')).toBeInTheDocument()
  })

  it('refetches when refreshKey ticks, so a sync or a save is reflected', async () => {
    const { rerender } = renderStats({ refreshKey: 0 })
    openPanel()
    await waitFor(() => expect(getStockStats).toHaveBeenCalledTimes(1))
    rerender(<StockStats hiddenCrawlerIds={[]} refreshKey={1} />)
    await waitFor(() => expect(getStockStats).toHaveBeenCalledTimes(2))
  })

  it('discards the breakdown on close, so reopening never shows the old numbers', async () => {
    renderStats()
    openPanel()
    await screen.findByRole('img', { name: /items by source/i })

    openPanel()
    expect(screen.queryByRole('img', { name: /items by source/i })).toBeNull()

    getStockStats.mockResolvedValue({
      total: 7, sources: [{ crawler_id: 9, site_name: 'Southern Lord', count: 7 }],
    })
    openPanel()
    // The stale ring is gone before the new one arrives, not swapped under it.
    expect(screen.queryByRole('img', { name: /items by source/i })).toBeNull()
    expect(await screen.findByText('Southern Lord')).toBeInTheDocument()
    expect(screen.queryByText('Nuclear Blast')).toBeNull()
  })

  it('says so rather than drawing an empty ring when nothing matches', async () => {
    getStockStats.mockResolvedValue({ total: 0, sources: [] })
    renderStats()
    openPanel()
    expect(await screen.findByText('No items in stock for this filter.')).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: /items by source/i })).not.toBeInTheDocument()
  })

  it('reports a failed request instead of showing a stale or empty breakdown', async () => {
    getStockStats.mockRejectedValue(new Error('boom'))
    renderStats()
    openPanel()
    expect(await screen.findByText('Couldn’t load the breakdown.')).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: /items by source/i })).not.toBeInTheDocument()
  })

  it('still lists every source in the legend when the ring folds its tail', async () => {
    const many: StockSourceCount[] = Array.from({ length: 12 }, (_, i) => ({
      crawler_id: i + 1, site_name: `Store ${i + 1}`, count: 12 - i,
    }))
    getStockStats.mockResolvedValue({ total: 78, sources: many })
    renderStats()
    openPanel()
    const donut = await screen.findByRole('img', { name: /items by source/i })
    // Seven named arcs plus one "Other", over the track circle.
    expect(donut.querySelectorAll('circle').length).toBe(9)
    for (const s of many) {
      expect(screen.getByText(s.site_name)).toBeInTheDocument()
    }
  })
})

describe('toSlices', () => {
  it('gives every source its own hue while they fit the palette', () => {
    const slices = toSlices(SOURCES)
    expect(slices.map((s) => s.label)).toEqual(['Nuclear Blast', 'Epitaph', 'Deathwish Inc'])
    expect(new Set(slices.map((s) => s.color)).size).toBe(3)
  })

  it('fills all eight slots before folding anything', () => {
    const eight: StockSourceCount[] = Array.from({ length: 8 }, (_, i) => ({
      crawler_id: i + 1, site_name: `Store ${i + 1}`, count: 1,
    }))
    const slices = toSlices(eight)
    expect(slices.length).toBe(8)
    expect(slices.some((s) => s.label.startsWith('Other'))).toBe(false)
  })

  it('folds the tail past the eighth into one Other wedge carrying its total', () => {
    const ten: StockSourceCount[] = Array.from({ length: 10 }, (_, i) => ({
      crawler_id: i + 1, site_name: `Store ${i + 1}`, count: 10 - i,
    }))
    const slices = toSlices(ten)
    expect(slices.length).toBe(8)
    const other = slices[7]
    // Seven keep their own hue; Stores 8-10 fold, carrying 3 + 2 + 1.
    expect(other.label).toBe('Other (3 sources)')
    expect(other.value).toBe(6)
  })

  it('assigns hues in the palette order, never cycling one for a later slot', () => {
    const many: StockSourceCount[] = Array.from({ length: 20 }, (_, i) => ({
      crawler_id: i + 1, site_name: `Store ${i + 1}`, count: 1,
    }))
    const colors = toSlices(many).map((s) => s.color)
    expect(new Set(colors).size).toBe(colors.length)
    expect(toSlices(SOURCES).map((s) => s.color)).toEqual(colors.slice(0, 3))
  })
})
