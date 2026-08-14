import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import StockBrowser from '../views/StockBrowser'
import RecordBrowser from '../views/RecordBrowser'
import { reconcileSelectedArtist } from '../views/artistSelection'

const { getStock, getStockArtists, getReleases, getArtists } = vi.hoisted(() => ({
  getStock: vi.fn(),
  getStockArtists: vi.fn(),
  getReleases: vi.fn(),
  getArtists: vi.fn(),
}))

vi.mock('../api/client', () => ({ getStock, getStockArtists, getReleases, getArtists }))

const NO_STOCK = { total: 0, page: 1, per_page: 250, items: [] }
const NO_RELEASES = { total: 0, page: 1, per_page: 250, releases: [] }

beforeEach(() => {
  vi.clearAllMocks()
  getStock.mockResolvedValue(NO_STOCK)
  getStockArtists.mockResolvedValue([])
  getReleases.mockResolvedValue(NO_RELEASES)
  getArtists.mockResolvedValue([])
  localStorage.clear()
})

const SELECTED = 'bg-white text-gray-950'

describe('reconcileSelectedArtist', () => {
  it('keeps a selection the list still offers', () => {
    expect(reconcileSelectedArtist(['Jets To Brazil', 'Nails'], 'Nails')).toBe('Nails')
  })

  it('follows the selected artist to a re-cased label', () => {
    expect(reconcileSelectedArtist(['Jets To Brazil'], 'Jets to Brazil')).toBe('Jets To Brazil')
  })

  it('clears a selection the list no longer contains at all', () => {
    expect(reconcileSelectedArtist(['Nails'], 'Jets to Brazil')).toBe('')
  })

  it('leaves an empty selection empty rather than inventing one', () => {
    expect(reconcileSelectedArtist(['Nails'], '')).toBe('')
  })
})

describe('canonical label changing while an artist is selected', () => {
  it('keeps the stock sidebar entry highlighted when a sync re-cases the label', async () => {
    getStockArtists.mockResolvedValue(['Jets to Brazil'])
    const { rerender } = render(<StockBrowser syncGeneration={0} />)

    const button = await screen.findByRole('button', { name: 'Jets to Brazil' })
    fireEvent.click(button)
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(
      expect.objectContaining({ artist: 'Jets to Brazil' })
    ))

    // A crawler's rows are replaced mid-sync, so the commonest casing -- and
    // with it the canonical label -- flips.
    getStockArtists.mockResolvedValue(['Jets To Brazil'])
    rerender(<StockBrowser syncGeneration={1} />)

    await waitFor(() => expect(
      screen.getByRole('button', { name: 'Jets To Brazil' }).className
    ).toContain(SELECTED))
    expect(screen.getByRole('button', { name: 'All' }).className).not.toContain(SELECTED)
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(
      expect.objectContaining({ artist: 'Jets To Brazil' })
    ))
  })

  it('falls back to All in the stock sidebar when the artist leaves the list', async () => {
    getStockArtists.mockResolvedValue(['Jets to Brazil'])
    const { rerender } = render(<StockBrowser syncGeneration={0} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Jets to Brazil' }))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(
      expect.objectContaining({ artist: 'Jets to Brazil' })
    ))

    getStockArtists.mockResolvedValue(['Nails'])
    rerender(<StockBrowser syncGeneration={1} />)

    await waitFor(() => expect(
      screen.getByRole('button', { name: 'All' }).className
    ).toContain(SELECTED))
    expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ artist: undefined }))
  })

  it('keeps the collection sidebar entry highlighted when a sync re-cases the label', async () => {
    getArtists.mockResolvedValue(['Jets to Brazil'])
    const { rerender } = render(<RecordBrowser scope="collection" syncGeneration={0} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Jets to Brazil' }))
    await waitFor(() => expect(getReleases).toHaveBeenCalledWith(
      expect.objectContaining({ artist: 'Jets to Brazil' })
    ))

    // The sync writes catalog, and the canonical label prefers catalog casing.
    getArtists.mockResolvedValue(['Jets To Brazil'])
    rerender(<RecordBrowser scope="collection" syncGeneration={1} />)

    await waitFor(() => expect(
      screen.getByRole('button', { name: 'Jets To Brazil' }).className
    ).toContain(SELECTED))
    await waitFor(() => expect(getReleases).toHaveBeenCalledWith(
      expect.objectContaining({ artist: 'Jets To Brazil' })
    ))
  })
})
