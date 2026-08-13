import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import StockBrowser from '../views/StockBrowser'

const items = [
  { id: 1, item_key: 'k1', is_own: true, artist: 'Rob Zombie', title: 'The Great Satan — Ghostly Black Vinyl', format: 'Vinyl', price: 31.99, currency: 'USD', url: 'https://shop.nuclearblast.com/products/rob-zombie', cover_image_url: 'https://cdn.shopify.com/rz-black.png', source: 'Nuclear Blast', last_seen: '2026-07-05T00:00:00Z', discogs_price: null },
  { id: 2, item_key: 'k2', is_own: true, artist: 'NAILS', title: 'Every Bridge Burning — Forest Green LP', format: 'Vinyl', price: 25.99, currency: 'USD', url: 'https://shop.nuclearblast.com/products/nails', cover_image_url: null, source: 'Nuclear Blast', last_seen: '2026-07-05T00:00:00Z', discogs_price: '42.50' },
]

const getStock = vi.fn()
const getStockArtists = vi.fn()

vi.mock('../api/client', () => ({
  getStock: (...args: unknown[]) => getStock(...args),
  getStockArtists: (...args: unknown[]) => getStockArtists(...args),
}))

beforeEach(() => {
  getStock.mockReset()
  getStockArtists.mockReset()
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
    fireEvent.click(screen.getByText(/^Source/))
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

  it('defaults to All, lists only All/Recommended (no Overlapping), and disables Recommended when unavailable', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    const select = screen.getByRole('combobox') as HTMLSelectElement
    expect(select.value).toBe('all')
    expect(Array.from(select.options).map((o) => o.text)).toEqual(['All', 'Recommended'])
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
    expect(getStockArtists).toHaveBeenLastCalledWith(undefined, false, [])
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'recommended' } })
    await waitFor(() => expect(getStockArtists).toHaveBeenLastCalledWith(undefined, true, []))
  })

  it('restores a previously-selected Recommended filter from localStorage', async () => {
    localStorage.setItem('stockFilter_store', 'recommended')
    render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('recommended')
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
    expect(getStockArtists).toHaveBeenLastCalledWith(undefined, false, [3])
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
    expect(getStockArtists).toHaveBeenCalledWith('all', false, [])
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
    await waitFor(() => expect(getStockArtists).toHaveBeenLastCalledWith('wantlist', false, []))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'collection' } })
    await waitFor(() => expect(getStockArtists).toHaveBeenLastCalledWith('collection', false, []))
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
    expect(getStockArtists).toHaveBeenCalledWith('all', false, [])
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

  it('scope="store" (default) keeps the All/Recommended dropdown and sends no libraryScope', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    const select = screen.getByRole('combobox') as HTMLSelectElement
    expect([...select.options].map((o) => o.value)).toEqual(['all', 'recommended'])
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
})
