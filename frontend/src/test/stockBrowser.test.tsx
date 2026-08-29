import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import StockBrowser from '../views/StockBrowser'
import type { Crawler } from '../api/types'

const items = [
  { id: 1, item_key: 'k1', is_own: true, artist: 'Rob Zombie', title: 'The Great Satan — Ghostly Black Vinyl', format: 'Vinyl', price: 31.99, currency: 'USD', url: 'https://shop.nuclearblast.com/products/rob-zombie', cover_image_url: 'https://cdn.shopify.com/rz-black.png', source: 'Nuclear Blast', last_seen: '2026-07-05T00:00:00Z', discogs_price: null, saved: false },
  { id: 2, item_key: 'k2', is_own: true, artist: 'NAILS', title: 'Every Bridge Burning — Forest Green LP', format: 'Vinyl', price: 25.99, currency: 'USD', url: 'https://shop.nuclearblast.com/products/nails', cover_image_url: null, source: 'Nuclear Blast', last_seen: '2026-07-05T00:00:00Z', discogs_price: '42.50', saved: false },
]

const getStock = vi.fn()
const getStockArtists = vi.fn()
const saveStockItem = vi.fn()
const unsaveStockItem = vi.fn()
const getStockStats = vi.fn()

vi.mock('../api/client', () => ({
  getStock: (...args: unknown[]) => getStock(...args),
  getStockArtists: (...args: unknown[]) => getStockArtists(...args),
  saveStockItem: (...args: unknown[]) => saveStockItem(...args),
  unsaveStockItem: (...args: unknown[]) => unsaveStockItem(...args),
  getStockStats: (...args: unknown[]) => getStockStats(...args),
}))

beforeEach(() => {
  getStock.mockReset()
  getStockArtists.mockReset()
  saveStockItem.mockReset()
  unsaveStockItem.mockReset()
  getStockStats.mockReset()
  getStockStats.mockResolvedValue({ total: 2, sources: [{ crawler_id: 4, site_name: 'Nuclear Blast', count: 2 }] })
  getStock.mockResolvedValue({ total: 2, page: 1, per_page: 250, items })
  getStockArtists.mockResolvedValue(['NAILS', 'Rob Zombie'])
  localStorage.clear()
})

// Both the sidebar and the table render an artist's name, so tests that only
// need to confirm data has loaded wait on a title instead — titles are unique
// and never appear in the sidebar.

describe('StockBrowser', () => {
  it('renders artist, title, format, price link, source, and thumbnail for each item', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.getAllByText('Rob Zombie').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Vinyl').length).toBe(2)
    const link = screen.getByText('$31.99') as HTMLAnchorElement
    expect(link.closest('a')?.getAttribute('href')).toBe('https://shop.nuclearblast.com/products/rob-zombie')
    expect(screen.getAllByText('Nuclear Blast').length).toBe(2)
    const thumbnail = screen.getByAltText('The Great Satan — Ghostly Black Vinyl') as HTMLImageElement
    expect(thumbnail.getAttribute('src')).toBe('https://cdn.shopify.com/rz-black.png')
  })

  it('gives the list-view thumbnail a min-width so it matches Collection/Wantlist sizing', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    const thumbnail = screen.getByAltText('The Great Satan — Ghostly Black Vinyl') as HTMLImageElement
    expect(thumbnail).toHaveClass('min-w-10')
  })

  it('renders a placeholder box when cover_image_url is null', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('Every Bridge Burning — Forest Green LP')).toBeTruthy())
    expect(screen.queryByAltText('Every Bridge Burning — Forest Green LP')).toBeNull()
  })

  it('shows an empty state when there are no items', async () => {
    getStock.mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] })
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText(/No in-stock items yet/)).toBeTruthy())
  })

  it('points a non-admin to a store sync, not the admin-only Refresh button', async () => {
    getStock.mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] })
    render(<StockBrowser />)
    await waitFor(() => expect(
      screen.getByText('No in-stock items yet. Check back after the next store sync.')
    ).toBeTruthy())
  })

  it('points an admin to the Store Management Refresh button', async () => {
    getStock.mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] })
    render(<StockBrowser isAdmin />)
    await waitFor(() => expect(
      screen.getByText('No in-stock items yet. Click Refresh under Store Management in Settings.')
    ).toBeTruthy())
  })

  it('does not show the empty state while the initial fetch is still pending', async () => {
    let resolveFetch: (v: any) => void = () => {}
    getStock.mockReturnValue(new Promise((resolve) => { resolveFetch = resolve }))
    render(<StockBrowser />)
    expect(screen.queryByText(/No in-stock items yet/)).toBeNull()
    resolveFetch({ total: 0, page: 1, per_page: 250, items: [] })
    await screen.findByText(/No in-stock items yet/)
  })

  it('searches by artist or title', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByPlaceholderText('Search artist or title…'), { target: { value: 'nails' } })
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ search: 'nails' })))
  })

  it('toggles sort order when a column header is clicked twice', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByText(/Cost/))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ sort: 'price', order: 'asc' })))
    fireEvent.click(screen.getByText(/Cost/))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ sort: 'price', order: 'desc' })))
  })

  it('sorts by format when the Format column header is clicked', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByText(/Format/))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ sort: 'format', order: 'asc' })))
  })

  it('sorts by source when the Source column header is clicked', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Sort by source' }))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ sort: 'source', order: 'asc' })))
  })

  it('renders an artist sidebar with All plus each distinct artist, and filters on click', async () => {
    render(<StockBrowser />)
    // Wait on a fetched artist, not on All -- All renders before the artist
    // fetch lands, so waiting on it proves nothing about the sidebar.
    await waitFor(() => expect(screen.getByRole('button', { name: 'NAILS' })).toBeTruthy())
    expect(screen.getByRole('button', { name: 'All' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Rob Zombie' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'NAILS' }))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ artist: 'NAILS' })))
  })

  it('defaults sort to title when a specific artist is selected, and back to artist for All', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'NAILS' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'NAILS' }))
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'title', order: 'asc' })))
    fireEvent.click(screen.getByRole('button', { name: 'All' }))
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'artist', order: 'asc' })))
  })

  it('switches to tile view and links tiles to the product page', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByTitle('Tile view'))
    await waitFor(() => {
      const tileLink = screen.getByText('The Great Satan — Ghostly Black Vinyl').closest('a')
      expect(tileLink?.getAttribute('href')).toBe('https://shop.nuclearblast.com/products/rob-zombie')
    })
  })

  it('defaults to All, lists All/Recommended/Saved/Overlapped, and disables Recommended when unavailable', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    const select = screen.getByRole('combobox') as HTMLSelectElement
    expect(select.value).toBe('all')
    expect(Array.from(select.options).map((o) => o.text)).toEqual(['All', 'Recommended', 'Saved', 'Overlapped'])
    expect((screen.getByRole('option', { name: 'All' }) as HTMLOptionElement).disabled).toBe(false)
    expect((screen.getByRole('option', { name: 'Recommended' }) as HTMLOptionElement).disabled).toBe(true)
  })

  it('enables Recommended when recommendedAvailable is true', async () => {
    render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect((screen.getByRole('option', { name: 'Recommended' }) as HTMLOptionElement).disabled).toBe(false)
  })

  it('resets filter to All when recommendedAvailable becomes false while Recommended is selected', async () => {
    localStorage.setItem('stockFilter_store', 'recommended')
    const { rerender } = render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('recommended')
    rerender(<StockBrowser recommendedAvailable={false} />)
    await waitFor(() => expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('all'))
  })

  it('filters to recommended items when Recommended is selected', async () => {
    render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'recommended' } })
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ recommended: true })))
  })

  it('refetches the artist sidebar scoped to recommended when Recommended is selected', async () => {
    render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(getStockArtists).toHaveBeenLastCalledWith({ libraryScope: undefined, recommended: false, saved: false, overlapped: false, hiddenCrawlerIds: [] })
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'recommended' } })
    await waitFor(() => expect(getStockArtists).toHaveBeenLastCalledWith({ libraryScope: undefined, recommended: true, saved: false, overlapped: false, hiddenCrawlerIds: [] }))
  })

  it('restores a previously-selected Recommended filter from localStorage', async () => {
    localStorage.setItem('stockFilter_store', 'recommended')
    render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('recommended')
  })

  it('renders a Saved option in the Store filter dropdown', async () => {
    render(<StockBrowser scope="store" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.getByRole('option', { name: 'Saved' })).toBeTruthy()
  })

  it('does not render a Saved option in the Track filter dropdown', async () => {
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.queryByRole('option', { name: 'Saved' })).toBeNull()
  })

  it('selecting Saved sends saved=true and no recommended param', async () => {
    render(<StockBrowser scope="store" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'saved' } })
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ saved: true, recommended: false })))
  })

  it('shows saved-specific empty-state copy under the Saved filter with no results', async () => {
    getStock.mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] })
    render(<StockBrowser scope="store" />)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'saved' } })
    await waitFor(() => expect(screen.getByText("You haven't saved anything yet.")).toBeTruthy())
  })

  it('does not render an Overlapped option in the Track filter dropdown', async () => {
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.queryByRole('option', { name: 'Overlapped' })).toBeNull()
  })

  it('selecting Overlapped sends overlapped=true and no other Store filter', async () => {
    render(<StockBrowser scope="store" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'overlapped' } })
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(
      expect.objectContaining({ overlapped: true, recommended: false, saved: false }),
    ))
  })

  it('refetches the artist sidebar scoped to overlapped when Overlapped is selected', async () => {
    render(<StockBrowser scope="store" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'overlapped' } })
    await waitFor(() => expect(getStockArtists).toHaveBeenLastCalledWith({ libraryScope: undefined, recommended: false, saved: false, overlapped: true, hiddenCrawlerIds: [] }))
  })

  it('shows overlapped-specific empty-state copy under the Overlapped filter with no results', async () => {
    getStock.mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] })
    render(<StockBrowser scope="store" />)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'overlapped' } })
    await waitFor(() => expect(screen.getByText('Nothing by an artist in your collection is in stock right now.')).toBeTruthy())
  })

  it('restores a previously-selected Overlapped filter from localStorage', async () => {
    localStorage.setItem('stockFilter_store', 'overlapped')
    render(<StockBrowser scope="store" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('overlapped')
  })

  it('shows a recommendation reason as a tooltip on the artist and title cells', async () => {
    getStock.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      items: [{ ...items[0], reason: 'Similar to your hardcore collection' }],
    })
    render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    const artistCell = screen.getAllByText('Rob Zombie').map((el) => el.closest('td')).find((td) => td)
    expect(artistCell?.getAttribute('title')).toBe('Similar to your hardcore collection')
    expect(screen.getByText('The Great Satan — Ghostly Black Vinyl').getAttribute('title')).toBe('Similar to your hardcore collection')
  })

  it('shows a recommendation reason as a tooltip on the tile-view artist and title text', async () => {
    getStock.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      items: [{ ...items[0], reason: 'Similar to your hardcore collection' }],
    })
    render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByTitle('Tile view'))
    await waitFor(() => {
      const artistText = screen.getAllByText('Rob Zombie').map((el) => (el.tagName === 'DIV' ? el : null)).find((el) => el)
      expect(artistText?.getAttribute('title')).toBe('Similar to your hardcore collection')
      expect(screen.getByText('The Great Satan — Ghostly Black Vinyl').getAttribute('title')).toBe('Similar to your hardcore collection')
    })
  })

  it('passes hiddenCrawlerIds through to getStock', async () => {
    render(<StockBrowser hiddenCrawlerIds={[3, 7]} />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ hiddenCrawlerIds: [3, 7] }))
  })

  it('refetches items and the artist sidebar when hiddenCrawlerIds changes', async () => {
    const { rerender } = render(<StockBrowser hiddenCrawlerIds={[]} />)
    await waitFor(() => expect(getStock).toHaveBeenCalledTimes(1))
    rerender(<StockBrowser hiddenCrawlerIds={[3]} />)
    await waitFor(() => expect(getStock).toHaveBeenCalledTimes(2))
    expect(getStockArtists).toHaveBeenLastCalledWith({ libraryScope: undefined, recommended: false, saved: false, overlapped: false, hiddenCrawlerIds: [3] })
  })

  it('resets to page 1 when hiddenCrawlerIds changes, with a single fetch (not stale-page-then-corrected)', async () => {
    getStock.mockResolvedValue({ total: 500, page: 1, per_page: 250, items })
    const { rerender } = render(<StockBrowser hiddenCrawlerIds={[]} />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByText('Next →'))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ page: 2 })))
    getStock.mockClear()
    rerender(<StockBrowser hiddenCrawlerIds={[3]} />)
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ page: 1, hiddenCrawlerIds: [3] })))
    expect(getStock).toHaveBeenCalledTimes(1)
  })

  it('persists the filter to localStorage under stockFilter_store and restores it on remount', async () => {
    const { unmount } = render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'recommended' } })
    await waitFor(() => expect(localStorage.getItem('stockFilter_store')).toBe('recommended'))
    unmount()
    render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('recommended'))
  })

  it('scope="track" sends libraryScope and shows an All/Collection/Wantlist dropdown', async () => {
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    const select = screen.getByRole('combobox') as HTMLSelectElement
    expect([...select.options].map((o) => o.value)).toEqual(['all', 'collection', 'wantlist'])
    expect(select.value).toBe('all')
    expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ libraryScope: 'all' }))
  })

  it('scope="track" sends libraryScope on the artist sidebar fetch too', async () => {
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(getStockArtists).toHaveBeenCalledWith({ libraryScope: 'all', recommended: false, saved: false, overlapped: false, hiddenCrawlerIds: [] })
  })

  it('changing the Track filter refetches both the items and the artist sidebar with the new libraryScope', async () => {
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'wantlist' } })
    await waitFor(() =>
      expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ libraryScope: 'wantlist' }))
    )
    // The sidebar has to narrow with the table, or clicking a collection-only
    // artist under Wantlist lands on an empty table.
    await waitFor(() => expect(getStockArtists).toHaveBeenLastCalledWith({ libraryScope: 'wantlist', recommended: false, saved: false, overlapped: false, hiddenCrawlerIds: [] }))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'collection' } })
    await waitFor(() => expect(getStockArtists).toHaveBeenLastCalledWith({ libraryScope: 'collection', recommended: false, saved: false, overlapped: false, hiddenCrawlerIds: [] }))
  })

  it('clears a selected artist when the Track filter changes, and re-highlights All', async () => {
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'NAILS' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'NAILS' }))
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ artist: 'NAILS' })))
    // The narrower filter may not list NAILS at all, so the selection has to go
    // with it -- otherwise artist= keeps going out with nothing highlighted.
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'wantlist' } })
    await waitFor(() =>
      expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ libraryScope: 'wantlist', artist: undefined, sort: 'artist', order: 'asc' }))
    )
    expect(screen.getByRole('button', { name: 'All' }).className).toContain('bg-white')
  })

  it('clears a selected artist when the Store filter changes too', async () => {
    render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'NAILS' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'NAILS' }))
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ artist: 'NAILS' })))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'recommended' } })
    await waitFor(() =>
      expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ recommended: true, artist: undefined }))
    )
    expect(screen.getByRole('button', { name: 'All' }).className).toContain('bg-white')
  })

  it('resets to page 1 when the Track filter changes', async () => {
    getStock.mockResolvedValue({ total: 500, page: 1, per_page: 250, items })
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByText('Next →'))
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 })))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'wantlist' } })
    await waitFor(() =>
      expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ libraryScope: 'wantlist', page: 1 }))
    )
  })

  it('persists the Track filter under stockFilter_track and restores it on remount', async () => {
    const { unmount } = render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'collection' } })
    await waitFor(() => expect(localStorage.getItem('stockFilter_track')).toBe('collection'))
    unmount()
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('collection'))
  })

  // A select whose value matches no option falls back to its first option, so
  // the rendered value alone can't tell a rejected stored value from an
  // accepted one -- the fetch and the rewritten key are what actually pin it.
  it('ignores a stored filter value that is not valid for the scope', async () => {
    // 'wishlist' is the backend spelling and never a valid filter value. Unlike
    // 'recommended' it isn't also swept up by the recommendedAvailable reset
    // effect, so the allow-set is the only thing that can reject it.
    localStorage.setItem('stockFilter_track', 'wishlist')
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('all')
    expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ libraryScope: 'all' }))
    expect(getStockArtists).toHaveBeenCalledWith({ libraryScope: 'all', recommended: false, saved: false, overlapped: false, hiddenCrawlerIds: [] })
    expect(localStorage.getItem('stockFilter_track')).toBe('all')
  })

  it('ignores a Store filter value stored under the Track key', async () => {
    localStorage.setItem('stockFilter_track', 'recommended')
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('all')
    expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ libraryScope: 'all' }))
  })

  it('ignores a stored Track filter value on the Store tab', async () => {
    localStorage.setItem('stockFilter_store', 'wantlist')
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('all')
    expect(localStorage.getItem('stockFilter_store')).toBe('all')
  })

  it('scope="store" (default) keeps the Store dropdown and sends no libraryScope', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    const select = screen.getByRole('combobox') as HTMLSelectElement
    expect([...select.options].map((o) => o.value)).toEqual(['all', 'recommended', 'saved', 'overlapped'])
    expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ libraryScope: undefined }))
  })

  it('renders the Price column under every Track filter value', async () => {
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.getByText(/Price/)).toBeTruthy()
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'wantlist' } })
    await waitFor(() => expect(screen.getByText(/Price/)).toBeTruthy())
  })

  it('shows a filter-specific empty state on the Track tab', async () => {
    getStock.mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] })
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText(/Nothing you're tracking is in stock/)).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'wantlist' } })
    await waitFor(() => expect(screen.getByText(/Nothing on your wantlist is in stock/)).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'collection' } })
    await waitFor(() => expect(screen.getByText(/Nothing in your collection is in stock/)).toBeTruthy())
  })

  it('shows a Recommended-specific empty state on the Store tab', async () => {
    getStock.mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] })
    render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByText(/No in-stock items yet/)).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'recommended' } })
    await waitFor(() => expect(screen.getByText(/Nothing recommended is in stock/)).toBeTruthy())
  })

  it('shows the filter-specific Track empty state in tile view too', async () => {
    getStock.mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] })
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText(/Nothing you're tracking is in stock/)).toBeTruthy())
    fireEvent.click(screen.getByTitle('Tile view'))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'wantlist' } })
    await waitFor(() => expect(screen.getByText(/Nothing on your wantlist is in stock/)).toBeTruthy())
  })

  // The backend pins the discogs_price sort subquery to collection scope, so
  // under Wantlist the column is all — and the sort silently degrades to artist
  // order. A dead control is worse than no control, so the header goes plain.
  it('renders the Price header as a sort control under All and Collection but plain text under Wantlist', async () => {
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    const priceHeader = () => screen.getByText(/Price/).closest('th') as HTMLTableCellElement
    expect(priceHeader().querySelector('button')).toBeTruthy()

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'wantlist' } })
    await waitFor(() => expect(priceHeader().querySelector('button')).toBeNull())
    expect(priceHeader().getAttribute('aria-sort')).toBeNull()

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'collection' } })
    await waitFor(() => expect(priceHeader().querySelector('button')).toBeTruthy())
  })

  it('does not sort by discogs_price when the plain Wantlist Price header is clicked', async () => {
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'wantlist' } })
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ libraryScope: 'wantlist' })))
    getStock.mockClear()
    fireEvent.click(screen.getByText(/Price/))
    await new Promise((r) => setTimeout(r, 0))
    expect(getStock).not.toHaveBeenCalled()
  })

  it('resets a discogs_price sort to artist when switching to the Wantlist filter', async () => {
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByText(/Price/))
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'discogs_price' })))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'wantlist' } })
    await waitFor(() =>
      expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ libraryScope: 'wantlist', sort: 'artist', order: 'asc' }))
    )
    // Deliberately not restored -- the reset is a real state change, not a
    // suppressed view of a sort that survives underneath.
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'all' } })
    await waitFor(() =>
      expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ libraryScope: 'all', sort: 'artist' }))
    )
  })

  it('keeps a non-price sort intact when switching to the Wantlist filter', async () => {
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByText(/^Title/))
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'title' })))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'wantlist' } })
    await waitFor(() =>
      expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ libraryScope: 'wantlist', sort: 'title' }))
    )
  })

  it('renders the Price column by default in Track scope', async () => {
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.getByText(/Price/)).toBeTruthy()
  })

  it('hides the Price column in Track scope when hasPriceField is false', async () => {
    render(<StockBrowser scope="track" hasPriceField={false} />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.queryByText(/Price/)).toBeNull()
  })

  it('narrows the empty-state colSpan in Track scope when hasPriceField is false', async () => {
    getStock.mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] })
    render(<StockBrowser scope="track" hasPriceField={false} />)
    const emptyRow = await screen.findByText(/Nothing you're tracking is in stock/)
    expect(emptyRow.closest('td')).toHaveAttribute('colSpan', '6')
  })

  it('does not render a Price column in Store scope even when hasPriceField is true', async () => {
    render(<StockBrowser hasPriceField />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.queryByText(/Price/)).toBeNull()
  })

  it('does not render a Price column in Store scope', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.queryByText(/Price/)).toBeNull()
  })

  it('renders a Price column in Track scope showing the matched discogs_price, or — when missing', async () => {
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.getByText(/Price/)).toBeTruthy()
    expect(screen.getByText('—')).toBeTruthy()
    expect(screen.getByText('42.50')).toBeTruthy()
  })

  it('sorts by discogs_price when the Price column header is clicked in Track scope', async () => {
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByText(/Price/))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ sort: 'discogs_price', order: 'asc' })))
  })

  it('resets a discogs_price sort to artist when hasPriceField flips to false in Track scope', async () => {
    const { rerender } = render(<StockBrowser scope="track" hasPriceField={true} />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByText(/Price/))
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'discogs_price' })))
    rerender(<StockBrowser scope="track" hasPriceField={false} />)
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'artist', order: 'asc' })))
  })

  it('persists the view mode to localStorage under collectionViewMode_store', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByTitle('Tile view'))
    await waitFor(() => expect(localStorage.getItem('collectionViewMode_store')).toBe('tiles'))
  })

  it('keeps Store and Track filter/view-mode selections independent in localStorage', async () => {
    const { unmount: unmountStore } = render(<StockBrowser scope="store" recommendedAvailable />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'recommended' } })
    fireEvent.click(screen.getByTitle('Tile view'))
    await waitFor(() => expect(localStorage.getItem('stockFilter_store')).toBe('recommended'))
    await waitFor(() => expect(localStorage.getItem('collectionViewMode_store')).toBe('tiles'))
    unmountStore()

    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByTitle('List view'))
    await waitFor(() => expect(localStorage.getItem('collectionViewMode_track')).toBe('list'))

    // Store's keys must be untouched by anything Track did.
    expect(localStorage.getItem('stockFilter_store')).toBe('recommended')
    expect(localStorage.getItem('collectionViewMode_store')).toBe('tiles')
    // Track's own key defaults to 'all' -- it never inherited Store's 'recommended'.
    expect(localStorage.getItem('stockFilter_track')).toBe('all')
  })

  it('renders a row for every item, including comparison rows, in list view', async () => {
    getStock.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      items: [
        items[0],
        { id: 'k1:Amazon', item_key: 'k1', is_own: false, artist: 'Rob Zombie', title: 'The Great Satan — Ghostly Black Vinyl', format: 'Vinyl', price: 29.99, currency: 'USD', url: 'https://amazon/x', cover_image_url: 'https://cdn.shopify.com/rz-black.png', source: 'Amazon', last_seen: '2026-07-05T00:00:00Z', reason: null },
      ],
    })
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getAllByText('The Great Satan — Ghostly Black Vinyl').length).toBe(2))
    expect(screen.getByText('$29.99')).toBeTruthy()
    expect(screen.getByText('Amazon')).toBeTruthy()
  })

  it('shows only the own row per item in tile view, even when comparison rows are present', async () => {
    getStock.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      items: [
        items[0],
        { id: 'k1:Amazon', item_key: 'k1', is_own: false, artist: 'Rob Zombie', title: 'The Great Satan — Ghostly Black Vinyl', format: 'Vinyl', price: 29.99, currency: 'USD', url: 'https://amazon/x', cover_image_url: null, source: 'Amazon', last_seen: '2026-07-05T00:00:00Z', reason: null },
      ],
    })
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getAllByText('The Great Satan — Ghostly Black Vinyl').length).toBe(2))
    fireEvent.click(screen.getByTitle('Tile view'))
    await waitFor(() => expect(screen.getAllByText('The Great Satan — Ghostly Black Vinyl').length).toBe(1))
  })

  it('renders a bookmark button per row in Store scope list view', async () => {
    render(<StockBrowser scope="store" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.getAllByTitle('Save for later').length).toBeGreaterThanOrEqual(1)
  })

  it('does not render a bookmark button in Track scope', async () => {
    render(<StockBrowser scope="track" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.queryByTitle('Save for later')).toBeNull()
    expect(screen.queryByTitle('Remove from saved')).toBeNull()
  })

  it('clicking the bookmark button calls saveStockItem with the item_key and flips the icon title', async () => {
    saveStockItem.mockResolvedValue({ saved: true })
    render(<StockBrowser scope="store" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    const button = screen.getAllByTitle('Save for later')[0]
    fireEvent.click(button)
    expect(saveStockItem).toHaveBeenCalledWith('k1')
    await waitFor(() => expect(screen.getAllByTitle('Remove from saved').length).toBeGreaterThanOrEqual(1))
  })

  it('ignores a second click while a save/unsave request is still in flight, even if it resolves before the first (reversed completion order)', async () => {
    // Regression test for the race Copilot flagged: click Save, then click
    // again (to Unsave) before the PUT settles. Copilot's report describes
    // the DELETE committing before the PUT, so the retry-driven reload would
    // faithfully report "saved" even though unsave was the user's actual
    // last action. The fix guards against the second click ever firing a
    // request at all -- the button is disabled and toggleSaved no-ops -- so
    // there is only ever one request in flight for a given item_key and
    // nothing to reconcile out of order. Asserting only one of
    // save/unsaveStockItem was called (not two, reconciled after the fact)
    // is the point of this test.
    let resolveSave: (v: unknown) => void = () => {}
    saveStockItem.mockReturnValue(new Promise((resolve) => { resolveSave = resolve }))
    // The initial load renders the item unsaved. The finally block bumps
    // retryTick once the save settles, which triggers a second getStock
    // call -- keep that response consistent with the optimistic "saved"
    // state so the reload doesn't itself overwrite the row back to unsaved
    // and confound the assertions below with an unrelated effect.
    getStock.mockResolvedValueOnce({ total: 2, page: 1, per_page: 250, items })
    getStock.mockResolvedValue({
      total: 2, page: 1, per_page: 250,
      items: [{ ...items[0], saved: true }, items[1]],
    })
    render(<StockBrowser scope="store" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())

    const button = screen.getAllByTitle('Save for later')[0]
    fireEvent.click(button)
    expect(saveStockItem).toHaveBeenCalledTimes(1)
    // The button is disabled while the save is pending, so a second click
    // does not fire unsaveStockItem (and the icon still shows the optimistic
    // "saved" state from the first click).
    await waitFor(() => expect(screen.getAllByTitle('Remove from saved').length).toBeGreaterThanOrEqual(1))
    const pendingButton = screen.getAllByTitle('Remove from saved')[0]
    expect(pendingButton).toBeDisabled()
    fireEvent.click(pendingButton)
    expect(unsaveStockItem).not.toHaveBeenCalled()
    expect(saveStockItem).toHaveBeenCalledTimes(1)

    // Now let the (only) in-flight request resolve -- even "late", after the
    // user's attempted second click -- and confirm the item is left saved,
    // matching the single request that was actually sent, not some
    // out-of-order DELETE-before-PUT outcome that never happened because the
    // second click was suppressed.
    resolveSave({ saved: true })
    await waitFor(() => expect(pendingButton).not.toBeDisabled())
    expect(screen.getAllByTitle('Remove from saved').length).toBeGreaterThanOrEqual(1)
    expect(saveStockItem).toHaveBeenCalledTimes(1)
    expect(unsaveStockItem).not.toHaveBeenCalled()
  })

  it('renders a bookmark button on the tile in tile view', async () => {
    render(<StockBrowser scope="store" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByTitle('Tile view'))
    await waitFor(() => expect(screen.getAllByTitle('Save for later').length).toBeGreaterThanOrEqual(1))
  })

  it('clicking the tile bookmark button calls saveStockItem and prevents the enclosing tile link from navigating', async () => {
    saveStockItem.mockResolvedValue({ saved: true })
    render(<StockBrowser scope="store" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByTitle('Tile view'))
    await waitFor(() => expect(screen.getAllByTitle('Save for later').length).toBeGreaterThanOrEqual(1))
    const button = screen.getAllByTitle('Save for later')[0]
    // The bookmark button sits inside an <a> that links out to the product
    // page. Its onClick calls e.preventDefault() specifically so that click
    // doesn't also trigger the anchor's navigation. preventDefault() on the
    // bubbling click event suppresses the anchor's default action regardless
    // of which descendant called it, so listening on the anchor itself and
    // checking defaultPrevented after the click proves the guard actually
    // ran -- a stronger assertion than just checking saveStockItem was
    // called, which would still pass even if preventDefault silently did
    // nothing.
    const anchor = button.closest('a')
    expect(anchor).not.toBeNull()
    let capturedEvent: Event | null = null
    anchor!.addEventListener('click', (e) => { capturedEvent = e })
    fireEvent.click(button)
    expect(saveStockItem).toHaveBeenCalledWith('k1')
    expect(capturedEvent).not.toBeNull()
    expect((capturedEvent as unknown as Event).defaultPrevented).toBe(true)
  })

  it('unsaving under the Saved filter removes the row', async () => {
    getStock.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      items: [{ ...items[0], saved: true }],
    })
    unsaveStockItem.mockResolvedValue({ saved: false })
    render(<StockBrowser scope="store" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'saved' } })
    const button = await screen.findByTitle('Remove from saved')
    fireEvent.click(button)
    await waitFor(() => expect(screen.queryByText('The Great Satan — Ghostly Black Vinyl')).toBeNull())
  })

  it('re-fetches from the server when a toggle fails, undoing the optimistic removal under the Saved filter', async () => {
    unsaveStockItem.mockRejectedValue(new Error('boom'))
    getStock.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      items: [{ ...items[0], saved: true }],
    })
    render(<StockBrowser scope="store" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'saved' } })
    const button = await screen.findByTitle('Remove from saved')
    const callsBefore = getStock.mock.calls.length
    fireEvent.click(button)
    // Optimistic update removes the row immediately.
    await waitFor(() => expect(screen.queryByText('The Great Satan — Ghostly Black Vinyl')).toBeNull())
    // unsaveStockItem rejects; the failure must trigger a re-fetch so the
    // phantom deletion self-corrects rather than persisting until an
    // unrelated refetch happens to occur. getStock keeps returning the same
    // saved item, so the row coming back proves load() actually ran on
    // failure, not just that it was called.
    await waitFor(() => expect(getStock.mock.calls.length).toBeGreaterThan(callsBefore))
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
  })

  it('refreshes the artist sidebar after a successful unsave under the Saved filter', async () => {
    // A successful unsave can drop an artist's only saved item, which should
    // stop that artist from being clickable-but-empty in the Saved sidebar.
    // That refresh only happens if toggleSaved's success path also bumps
    // retryTick (not just its failure path) -- see StockBrowser.tsx.
    getStock.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      items: [{ ...items[0], saved: true }],
    })
    unsaveStockItem.mockResolvedValue({ saved: false })
    render(<StockBrowser scope="store" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'saved' } })
    const button = await screen.findByTitle('Remove from saved')
    const artistCallsBefore = getStockArtists.mock.calls.length
    fireEvent.click(button)
    await waitFor(() => expect(getStockArtists.mock.calls.length).toBeGreaterThan(artistCallsBefore))
  })

  it('does not let a failed toggle retry clobber a newer search-driven load (race fix)', async () => {
    // Regression test for the race Copilot flagged: a failure-recovery reload
    // triggered by an in-flight toggle must not win against a load started
    // under load-identity state (search, in this case) the user changed
    // *after* the toggle failed. The fix routes the recovery through
    // retryTick -> the same load effect -> the same isLatest guard every
    // other trigger uses, so the retry's response is stale by the time a
    // newer search-driven response lands and must not commit.
    unsaveStockItem.mockRejectedValue(new Error('boom'))
    const bothItems = [{ ...items[0], saved: true }, items[1]]
    let callCount = 0
    let resolveRetryCall: (v: unknown) => void = () => {}
    let resolveSearchCall: (v: unknown) => void = () => {}
    getStock.mockImplementation(() => {
      callCount += 1
      if (callCount === 1) {
        return Promise.resolve({ total: 2, page: 1, per_page: 250, items: bothItems })
      }
      if (callCount === 2) {
        // The retry load triggered by the failed toggle.
        return new Promise((resolve) => { resolveRetryCall = resolve })
      }
      // The load triggered by the search box change that follows.
      return new Promise((resolve) => { resolveSearchCall = resolve })
    })

    render(<StockBrowser scope="store" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())

    const button = screen.getAllByTitle('Remove from saved')[0]
    fireEvent.click(button)
    // Toggle failure bumps retryTick, which issues the second (retry) getStock call.
    await waitFor(() => expect(callCount).toBe(2))

    // Before the retry resolves, the user changes the search box -- a newer
    // load-identity change that starts its own, newer request.
    fireEvent.change(screen.getByPlaceholderText('Search artist or title…'), { target: { value: 'nails' } })
    await waitFor(() => expect(callCount).toBe(3))

    // Resolve the newer (search) request first, then let the stale retry
    // request resolve after it.
    resolveSearchCall({ total: 1, page: 1, per_page: 250, items: [items[1]] })
    await waitFor(() => expect(screen.getByText('Every Bridge Burning — Forest Green LP')).toBeTruthy())
    expect(screen.queryByText('The Great Satan — Ghostly Black Vinyl')).toBeNull()

    resolveRetryCall({ total: 2, page: 1, per_page: 250, items: bothItems })
    // Give the stale retry's resolution a chance to (wrongly) commit if the
    // race guard were broken.
    await new Promise((r) => setTimeout(r, 0))
    expect(screen.queryByText('The Great Satan — Ghostly Black Vinyl')).toBeNull()
    expect(screen.getByText('Every Bridge Burning — Forest Green LP')).toBeTruthy()
  })
})

const CRAWLERS: Crawler[] = [
  { id: 5, site_name: 'Epitaph', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: null, genre: 'punk' },
]

describe('StockBrowser Source filter', () => {
  it('renders the Source button in the header', async () => {
    render(<StockBrowser crawlers={CRAWLERS} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Source' })).toBeInTheDocument())
  })

  it('calls onHiddenCrawlerIdsChange when a store checkbox is toggled', async () => {
    const onHiddenCrawlerIdsChange = vi.fn()
    render(<StockBrowser crawlers={CRAWLERS} onHiddenCrawlerIdsChange={onHiddenCrawlerIdsChange} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Source' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Epitaph' }))
    expect(onHiddenCrawlerIdsChange).toHaveBeenCalledWith([5])
  })

  it('disables the Source button while the hidden set has not loaded yet', async () => {
    render(<StockBrowser crawlers={CRAWLERS} hiddenCrawlerIdsLoaded={false} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Source' })).toBeDisabled())
  })

  it('does not fetch stock or artists until the hidden set has loaded, then fetches once it does', async () => {
    const { rerender } = render(<StockBrowser crawlers={CRAWLERS} hiddenCrawlerIdsLoaded={false} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Source' })).toBeDisabled())
    expect(getStock).not.toHaveBeenCalled()
    expect(getStockArtists).not.toHaveBeenCalled()

    rerender(<StockBrowser crawlers={CRAWLERS} hiddenCrawlerIdsLoaded={true} />)
    await waitFor(() => expect(getStock).toHaveBeenCalled())
    expect(getStockArtists).toHaveBeenCalled()
  })

  it('offers Stats beside Source on the store tab, and not on the track tab', async () => {
    const { unmount } = render(<StockBrowser crawlers={CRAWLERS} />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    const source = screen.getByRole('button', { name: 'Source' })
    const stats = screen.getByRole('button', { name: 'Stats' })
    // Each sits in its own anchor wrapper; Stats' wrapper follows Source's.
    expect(source.parentElement!.nextElementSibling).toBe(stats.parentElement)
    unmount()

    render(<StockBrowser scope="track" crawlers={CRAWLERS} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Source' })).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Stats' })).toBeNull()
  })

  it('omits the toolbar item count on the Store tab, since Stats already shows the total, but keeps it on Track', async () => {
    const { unmount } = render(<StockBrowser crawlers={CRAWLERS} />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.queryByText(/^2 items$/)).toBeNull()
    unmount()

    render(<StockBrowser scope="track" crawlers={CRAWLERS} />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.getByText(/^2 items$/)).toBeTruthy()
  })

  it('does not refetch the breakdown for a listing_changed, which cannot move a stock_items count', async () => {
    // syncGeneration is the union the item list rides: it includes
    // listing_changed, broadcast to every connected user on every marketplace
    // write. The panel counts stock_items, which such an event never touches.
    const { rerender } = render(
      <StockBrowser crawlers={CRAWLERS} syncGeneration={0} inventoryGeneration={0} judgmentGeneration={0} />
    )
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Stats' }))
    await waitFor(() => expect(getStockStats).toHaveBeenCalledTimes(1))

    rerender(
      <StockBrowser crawlers={CRAWLERS} syncGeneration={1} inventoryGeneration={0} judgmentGeneration={0} />
    )
    await waitFor(() => expect(getStock).toHaveBeenCalledTimes(2))
    expect(getStockStats).toHaveBeenCalledTimes(1)

    // A real inventory write does refetch it.
    rerender(
      <StockBrowser crawlers={CRAWLERS} syncGeneration={2} inventoryGeneration={1} judgmentGeneration={0} />
    )
    await waitFor(() => expect(getStockStats).toHaveBeenCalledTimes(2))
  })

  it('refetches the breakdown for a judgment only under the Recommended filter', async () => {
    const { rerender } = render(
      <StockBrowser crawlers={CRAWLERS} recommendedAvailable inventoryGeneration={0} judgmentGeneration={0} />
    )
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Stats' }))
    await waitFor(() => expect(getStockStats).toHaveBeenCalledTimes(1))

    // Under All, a judgment cannot change the count.
    rerender(
      <StockBrowser crawlers={CRAWLERS} recommendedAvailable inventoryGeneration={0} judgmentGeneration={1} />
    )
    await new Promise((r) => setTimeout(r, 0))
    expect(getStockStats).toHaveBeenCalledTimes(1)

    fireEvent.change(screen.getByDisplayValue('All'), { target: { value: 'recommended' } })
    await waitFor(() => expect(getStockStats).toHaveBeenCalledTimes(2))
    rerender(
      <StockBrowser crawlers={CRAWLERS} recommendedAvailable inventoryGeneration={0} judgmentGeneration={2} />
    )
    await waitFor(() => expect(getStockStats).toHaveBeenCalledTimes(3))
  })

  it('breaks the store tab down by source under the filters the list is showing', async () => {
    render(<StockBrowser crawlers={CRAWLERS} hiddenCrawlerIds={[5]} />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByPlaceholderText('Search artist or title…'), { target: { value: 'zombie' } })
    fireEvent.change(screen.getByDisplayValue('All'), { target: { value: 'saved' } })

    fireEvent.click(screen.getByRole('button', { name: 'Stats' }))
    await waitFor(() => expect(getStockStats).toHaveBeenCalled())
    expect(getStockStats).toHaveBeenLastCalledWith(expect.objectContaining({
      search: 'zombie', saved: true, recommended: false, overlapped: false, hiddenCrawlerIds: [5],
    }))
    expect(await screen.findByRole('img', { name: /items by source/i })).toBeInTheDocument()
  })
})
