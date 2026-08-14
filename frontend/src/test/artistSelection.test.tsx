import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
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

  it('clears rather than following a label JS folds equal but Postgres does not', () => {
    // "İsis" (U+0130, 4 chars) and "i̇sis" (i + U+0307, 5 chars) fold to the
    // same string in JS but to different groups under Postgres LOWER(), so
    // following the second would hand the filter to a different artist. Equal
    // length is what a pure change of case looks like; this isn't one.
    const precomposed = 'İsis'
    const decomposed = 'i̇sis'
    expect(precomposed.toLowerCase()).toBe(decomposed.toLowerCase())
    expect(reconcileSelectedArtist([decomposed], precomposed)).toBe('')
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
    // Waited, not asserted straight after the highlight: clearing the
    // selection re-renders first and only then re-runs the load effect, so on a
    // loaded runner the last recorded call is still the pre-clear one.
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(
      expect.objectContaining({ artist: undefined })
    ))
  })

  it('ignores an artist list from a superseded stock request', async () => {
    // syncGeneration ticks faster than a round-trip, so an older request can
    // resolve last. If its list were committed, the reconciliation would clear
    // a selection the newest response still lists.
    let resolveStale: (list: string[]) => void = () => {}
    getStockArtists
      .mockResolvedValueOnce(['Jets to Brazil'])
      .mockImplementationOnce(() => new Promise<string[]>((res) => { resolveStale = res }))
      .mockResolvedValueOnce(['Jets to Brazil'])
    const { rerender } = render(<StockBrowser syncGeneration={0} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Jets to Brazil' }))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(
      expect.objectContaining({ artist: 'Jets to Brazil' })
    ))

    rerender(<StockBrowser syncGeneration={1} />)
    rerender(<StockBrowser syncGeneration={2} />)
    await waitFor(() => expect(getStockArtists).toHaveBeenCalledTimes(3))

    // Flushed through act, so the assertions below run after React would have
    // applied the stale list -- not merely before it had the chance.
    await act(async () => { resolveStale([]) })
    expect(screen.getByRole('button', { name: 'Jets to Brazil' }).className).toContain(SELECTED)
    expect(screen.getByRole('button', { name: 'All' }).className).not.toContain(SELECTED)
  })

  it('ignores an artist list from a superseded collection request', async () => {
    let resolveStale: (list: string[]) => void = () => {}
    getArtists
      .mockResolvedValueOnce(['Jets to Brazil'])
      .mockImplementationOnce(() => new Promise<string[]>((res) => { resolveStale = res }))
      .mockResolvedValueOnce(['Jets to Brazil'])
    const { rerender } = render(<RecordBrowser scope="collection" syncGeneration={0} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Jets to Brazil' }))
    await waitFor(() => expect(getReleases).toHaveBeenCalledWith(
      expect.objectContaining({ artist: 'Jets to Brazil' })
    ))

    rerender(<RecordBrowser scope="collection" syncGeneration={1} />)
    rerender(<RecordBrowser scope="collection" syncGeneration={2} />)
    await waitFor(() => expect(getArtists).toHaveBeenCalledTimes(3))

    await act(async () => { resolveStale([]) })
    expect(screen.getByRole('button', { name: 'Jets to Brazil' }).className).toContain(SELECTED)
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
