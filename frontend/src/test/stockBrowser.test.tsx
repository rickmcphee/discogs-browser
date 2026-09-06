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
  getStock.mockResolvedValue({ total: 2, row_total: 2, page: 1, per_page: 250, items })
  getStockArtists.mockResolvedValue(['NAILS', 'Rob Zombie'])
  localStorage.clear()
})

// The row-set filter lives behind a "Filter: …" popover now, not a <select>.
// The trigger reads the current value, so assertions read it back off the
// trigger's text rather than opening the panel; choosing a value opens the
// panel (idempotently -- it stays open across choices) and clicks the radio.
const FILTER_LABELS: Record<string, string> = {
  all: 'All', recommended: 'Recommended', saved: 'Saved', overlapped: 'Overlapped',
  collection: 'Collection', wantlist: 'Wantlist',
}
const filterButton = () => screen.getByRole('button', { name: /^Filter:/ })
function openFilter() {
  const button = filterButton()
  if (button.getAttribute('aria-expanded') !== 'true') fireEvent.click(button)
}
function chooseFilter(value: string) {
  openFilter()
  fireEvent.click(screen.getByRole('radio', { name: FILTER_LABELS[value] }))
}
function filterValue(): string {
  const label = (filterButton().textContent ?? '').replace(/^Filter: /, '').replace(/ · Cheapest$/, '')
  const entry = Object.entries(FILTER_LABELS).find(([, l]) => l === label)
  return entry ? entry[0] : label
}
function cheapestBox(): HTMLInputElement {
  openFilter()
  return screen.getByRole('checkbox', { name: 'Cheapest' }) as HTMLInputElement
}

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
    getStock.mockResolvedValue({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] })
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText(/No in-stock items yet/)).toBeTruthy())
  })

  it('points a non-admin to a store sync, not the admin-only Refresh button', async () => {
    getStock.mockResolvedValue({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] })
    render(<StockBrowser />)
    await waitFor(() => expect(
      screen.getByText('No in-stock items yet. Check back after the next store sync.')
    ).toBeTruthy())
  })

  it('points an admin to the Store Management Refresh button', async () => {
    getStock.mockResolvedValue({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] })
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
    resolveFetch({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] })
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

  // #243: reported as the Cost header appearing to do nothing once a search
  // term had narrowed the table. `sort` and `search` are independent pieces
  // of `load`'s request object (see StockBrowser.tsx), so clicking Cost after
  // typing a search term must still request a price sort -- not silently drop
  // it, and not drop the search term either.
  it('keeps the search term when the Cost column header is clicked after searching', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByPlaceholderText('Search artist or title…'), { target: { value: 'nails' } })
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ search: 'nails' })))
    fireEvent.click(screen.getByText(/Cost/))
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(
      expect.objectContaining({ search: 'nails', sort: 'price', order: 'asc' })
    ))
  })

  // The request-params assertion above would still pass even if the
  // price-sorted response never made it to the screen (e.g. a stale response
  // from the pre-sort search winning a race). StockBrowser renders rows in
  // whatever order the server returns them in -- it does no client-side
  // sorting -- so pin the actual regression: clicking Cost after a search
  // must re-render the table in the order of *that* response.
  it('re-renders rows in price order once Cost is clicked after searching, not the pre-sort order', async () => {
    const zombie = items[0] // price 31.99
    const nails = items[1] // price 25.99
    const { container } = render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())

    getStock.mockResolvedValue({ total: 2, row_total: 2, page: 1, per_page: 250, items: [zombie, nails] })
    fireEvent.change(screen.getByPlaceholderText('Search artist or title…'), { target: { value: 'e' } })
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ search: 'e' })))
    await waitFor(() => expect(screen.getAllByRole('row').length).toBe(3)) // header + 2 items

    // The server's price-ascending response for the same search: the cheaper
    // NAILS item first, the pricier Rob Zombie item second -- reversed from
    // both the pre-sort order above and the default fixture order.
    getStock.mockResolvedValue({ total: 2, row_total: 2, page: 1, per_page: 250, items: [nails, zombie] })
    fireEvent.click(screen.getByText(/Cost/))
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(
      expect.objectContaining({ search: 'e', sort: 'price', order: 'asc' })
    ))

    await waitFor(() => {
      const tbody = container.querySelector('tbody')!.textContent!
      expect(tbody.indexOf('Every Bridge Burning')).toBeGreaterThanOrEqual(0)
      expect(tbody.indexOf('Every Bridge Burning')).toBeLessThan(tbody.indexOf('The Great Satan'))
    })
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

  it('defaults to All, lists All/Recommended/Saved/Overlapped/Collection/Wantlist, and disables Recommended when unavailable', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(filterValue()).toBe('all')
    openFilter()
    expect(screen.getAllByRole('radio').map((r) => (r as HTMLInputElement).value)).toEqual(['all', 'recommended', 'saved', 'overlapped', 'collection', 'wantlist'])
    expect((screen.getByRole('radio', { name: 'All' }) as HTMLInputElement).disabled).toBe(false)
    expect((screen.getByRole('radio', { name: 'Recommended' }) as HTMLInputElement).disabled).toBe(true)
  })

  it('enables Recommended when recommendedAvailable is true', async () => {
    render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    openFilter()
    expect((screen.getByRole('radio', { name: 'Recommended' }) as HTMLInputElement).disabled).toBe(false)
  })

  it('resets filter to All when recommendedAvailable becomes false while Recommended is selected', async () => {
    localStorage.setItem('stockFilter_store', 'recommended')
    const { rerender } = render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(filterValue()).toBe('recommended')
    rerender(<StockBrowser recommendedAvailable={false} />)
    await waitFor(() => expect(filterValue()).toBe('all'))
  })

  it('filters to recommended items when Recommended is selected', async () => {
    render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    chooseFilter('recommended')
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ recommended: true })))
  })

  it('refetches the artist sidebar scoped to recommended when Recommended is selected', async () => {
    render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(getStockArtists).toHaveBeenLastCalledWith({ libraryScope: undefined, recommended: false, saved: false, overlapped: false, hiddenCrawlerIds: [] })
    chooseFilter('recommended')
    await waitFor(() => expect(getStockArtists).toHaveBeenLastCalledWith({ libraryScope: undefined, recommended: true, saved: false, overlapped: false, hiddenCrawlerIds: [] }))
  })

  it('restores a previously-selected Recommended filter from localStorage', async () => {
    localStorage.setItem('stockFilter_store', 'recommended')
    render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(filterValue()).toBe('recommended')
  })

  it('renders a Saved option in the Store filter dropdown', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    openFilter()
    expect(screen.getByRole('radio', { name: 'Saved' })).toBeTruthy()
  })

  it('selecting Saved sends saved=true and no recommended param', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    chooseFilter('saved')
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ saved: true, recommended: false })))
  })

  it('shows saved-specific empty-state copy under the Saved filter with no results', async () => {
    getStock.mockResolvedValue({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] })
    render(<StockBrowser />)
    chooseFilter('saved')
    await waitFor(() => expect(screen.getByText("You haven't saved anything yet.")).toBeTruthy())
  })

  it('selecting Overlapped sends overlapped=true and no other Store filter', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    chooseFilter('overlapped')
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(
      expect.objectContaining({ overlapped: true, recommended: false, saved: false }),
    ))
  })

  it('refetches the artist sidebar scoped to overlapped when Overlapped is selected', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    chooseFilter('overlapped')
    await waitFor(() => expect(getStockArtists).toHaveBeenLastCalledWith({ libraryScope: undefined, recommended: false, saved: false, overlapped: true, hiddenCrawlerIds: [] }))
  })

  it('shows overlapped-specific empty-state copy under the Overlapped filter with no results', async () => {
    getStock.mockResolvedValue({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] })
    render(<StockBrowser />)
    chooseFilter('overlapped')
    await waitFor(() => expect(screen.getByText('Nothing by an artist in your collection is in stock right now.')).toBeTruthy())
  })

  it('restores a previously-selected Overlapped filter from localStorage', async () => {
    localStorage.setItem('stockFilter_store', 'overlapped')
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(filterValue()).toBe('overlapped')
  })

  it('shows a recommendation reason as a tooltip on the artist and title cells', async () => {
    getStock.mockResolvedValue({
      total: 1, row_total: 1, page: 1, per_page: 250,
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
      total: 1, row_total: 1, page: 1, per_page: 250,
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
    getStock.mockResolvedValue({ total: 500, row_total: 500, page: 1, per_page: 250, items })
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
    chooseFilter('recommended')
    await waitFor(() => expect(localStorage.getItem('stockFilter_store')).toBe('recommended'))
    unmount()
    render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(filterValue()).toBe('recommended'))
  })

  it('lists Collection and Wantlist after the Store filters, and sends libraryScope only for those', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(filterValue()).toBe('all')
    expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ libraryScope: undefined }))
    openFilter()
    expect(screen.getAllByRole('radio').map((r) => (r as HTMLInputElement).value))
      .toEqual(['all', 'recommended', 'saved', 'overlapped', 'collection', 'wantlist'])
    chooseFilter('collection')
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ libraryScope: 'collection' })))
  })

  it('sends libraryScope on the artist sidebar fetch too', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    chooseFilter('collection')
    await waitFor(() => expect(getStockArtists).toHaveBeenLastCalledWith({ libraryScope: 'collection', recommended: false, saved: false, overlapped: false, hiddenCrawlerIds: [] }))
  })

  it('changing between Collection and Wantlist refetches both the items and the artist sidebar with the new libraryScope', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    chooseFilter('wantlist')
    await waitFor(() =>
      expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ libraryScope: 'wantlist' }))
    )
    // The sidebar has to narrow with the table, or clicking a collection-only
    // artist under Wantlist lands on an empty table.
    await waitFor(() => expect(getStockArtists).toHaveBeenLastCalledWith({ libraryScope: 'wantlist', recommended: false, saved: false, overlapped: false, hiddenCrawlerIds: [] }))
    chooseFilter('collection')
    await waitFor(() => expect(getStockArtists).toHaveBeenLastCalledWith({ libraryScope: 'collection', recommended: false, saved: false, overlapped: false, hiddenCrawlerIds: [] }))
  })

  it('clears a selected artist when a library filter is chosen, and re-highlights All', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'NAILS' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'NAILS' }))
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ artist: 'NAILS' })))
    // The narrower filter may not list NAILS at all, so the selection has to go
    // with it -- otherwise artist= keeps going out with nothing highlighted.
    chooseFilter('wantlist')
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
    chooseFilter('recommended')
    await waitFor(() =>
      expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ recommended: true, artist: undefined }))
    )
    expect(screen.getByRole('button', { name: 'All' }).className).toContain('bg-white')
  })

  it('resets to page 1 when a library filter is chosen', async () => {
    getStock.mockResolvedValue({ total: 500, row_total: 500, page: 1, per_page: 250, items })
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByText('Next →'))
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 })))
    chooseFilter('wantlist')
    await waitFor(() =>
      expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ libraryScope: 'wantlist', page: 1 }))
    )
  })

  it('persists a library filter under stockFilter_store and restores it on remount', async () => {
    const { unmount } = render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    chooseFilter('collection')
    await waitFor(() => expect(localStorage.getItem('stockFilter_store')).toBe('collection'))
    unmount()
    render(<StockBrowser />)
    await waitFor(() => expect(filterValue()).toBe('collection'))
    expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ libraryScope: 'collection' }))
  })

  // A select whose value matches no option falls back to its first option, so
  // the rendered value alone can't tell a rejected stored value from an
  // accepted one -- the fetch and the rewritten key are what actually pin it.
  it('ignores a stored filter value that is not a filter', async () => {
    // 'wishlist' is the backend spelling and never a valid filter value. Unlike
    // 'recommended' it isn't also swept up by the recommendedAvailable reset
    // effect, so the allow-set is the only thing that can reject it.
    localStorage.setItem('stockFilter_store', 'wishlist')
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(filterValue()).toBe('all')
    expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ libraryScope: undefined }))
    expect(getStockArtists).toHaveBeenCalledWith({ libraryScope: undefined, recommended: false, saved: false, overlapped: false, hiddenCrawlerIds: [] })
    expect(localStorage.getItem('stockFilter_store')).toBe('all')
  })

  it('restores a stored Wantlist filter, which was the Track tab\'s and is now a Store filter', async () => {
    localStorage.setItem('stockFilter_store', 'wantlist')
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(filterValue()).toBe('wantlist')
    expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ libraryScope: 'wantlist' }))
  })

  it('sends no libraryScope under the non-library filters', async () => {
    render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    for (const value of ['recommended', 'saved', 'overlapped']) {
      chooseFilter(value)
      await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ [value]: true, libraryScope: undefined })))
    }
  })

  it('renders the Price column under Collection only', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.queryByText(/Price/)).toBeNull()
    chooseFilter('collection')
    await waitFor(() => expect(screen.getByText(/Price/)).toBeTruthy())
    // A wantlist row has no paid price, so the column would be all dashes.
    chooseFilter('wantlist')
    await waitFor(() => expect(screen.queryByText(/Price/)).toBeNull())
  })

  it('shows a filter-specific empty state under the library filters', async () => {
    getStock.mockResolvedValue({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] })
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText(/No in-stock items yet/)).toBeTruthy())
    chooseFilter('wantlist')
    await waitFor(() => expect(screen.getByText(/Nothing on your wantlist is in stock/)).toBeTruthy())
    chooseFilter('collection')
    await waitFor(() => expect(screen.getByText(/Nothing in your collection is in stock/)).toBeTruthy())
  })

  it('shows a Recommended-specific empty state on the Store tab', async () => {
    getStock.mockResolvedValue({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] })
    render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByText(/No in-stock items yet/)).toBeTruthy())
    chooseFilter('recommended')
    await waitFor(() => expect(screen.getByText(/Nothing recommended is in stock/)).toBeTruthy())
  })

  it('shows the filter-specific library empty state in tile view too', async () => {
    getStock.mockResolvedValue({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] })
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText(/No in-stock items yet/)).toBeTruthy())
    fireEvent.click(screen.getByTitle('Tile view'))
    chooseFilter('wantlist')
    await waitFor(() => expect(screen.getByText(/Nothing on your wantlist is in stock/)).toBeTruthy())
  })

  // The backend pins the discogs_price sort subquery to collection scope, so
  // under Wantlist the column is all — and the sort silently degrades to artist
  // order. A dead control is worse than no control, so the header goes plain.
  it('resets a discogs_price sort to artist when leaving the Collection filter', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    chooseFilter('collection')
    await waitFor(() => expect(screen.getByText(/Price/)).toBeTruthy())
    fireEvent.click(screen.getByText(/Price/))
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'discogs_price' })))
    chooseFilter('wantlist')
    await waitFor(() =>
      expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ libraryScope: 'wantlist', sort: 'artist', order: 'asc' }))
    )
    // Deliberately not restored -- the reset is a real state change, not a
    // suppressed view of a sort that survives underneath.
    chooseFilter('collection')
    await waitFor(() =>
      expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ libraryScope: 'collection', sort: 'artist' }))
    )
  })

  it('keeps a non-price sort intact when switching to the Wantlist filter', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByText(/^Title/))
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'title' })))
    chooseFilter('wantlist')
    await waitFor(() =>
      expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ libraryScope: 'wantlist', sort: 'title' }))
    )
  })

  it('hides the Price column under Collection when hasPriceField is false', async () => {
    render(<StockBrowser hasPriceField={false} />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    chooseFilter('collection')
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ libraryScope: 'collection' })))
    expect(screen.queryByText(/Price/)).toBeNull()
  })

  it('widens the empty-state colSpan by the Price column under Collection', async () => {
    getStock.mockResolvedValue({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] })
    render(<StockBrowser />)
    const emptyRow = await screen.findByText(/No in-stock items yet/)
    expect(emptyRow.closest('td')).toHaveAttribute('colSpan', '7')
    chooseFilter('collection')
    const collectionRow = await screen.findByText(/Nothing in your collection is in stock/)
    expect(collectionRow.closest('td')).toHaveAttribute('colSpan', '8')
  })

  it('does not render a Price column under All even when hasPriceField is true', async () => {
    render(<StockBrowser hasPriceField />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.queryByText(/Price/)).toBeNull()
  })

  it('renders a Price column under Collection showing the matched discogs_price, or — when missing', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    chooseFilter('collection')
    await waitFor(() => expect(screen.getByText(/Price/)).toBeTruthy())
    expect(screen.getByText('—')).toBeTruthy()
    expect(screen.getByText('42.50')).toBeTruthy()
  })

  it('sorts by discogs_price when the Price column header is clicked under Collection', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    chooseFilter('collection')
    await waitFor(() => expect(screen.getByText(/Price/)).toBeTruthy())
    fireEvent.click(screen.getByText(/Price/))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ sort: 'discogs_price', order: 'asc' })))
  })

  it('resets a discogs_price sort to artist when hasPriceField flips to false under Collection', async () => {
    const { rerender } = render(<StockBrowser hasPriceField={true} />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    chooseFilter('collection')
    await waitFor(() => expect(screen.getByText(/Price/)).toBeTruthy())
    fireEvent.click(screen.getByText(/Price/))
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'discogs_price' })))
    rerender(<StockBrowser hasPriceField={false} />)
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'artist', order: 'asc' })))
  })

  it('persists the view mode to localStorage under collectionViewMode_store', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByTitle('Tile view'))
    await waitFor(() => expect(localStorage.getItem('collectionViewMode_store')).toBe('tiles'))
  })

  it('paginates on row_total, not the item count, so a flattened Cost sort reaches its later pages', async () => {
    // Under a Cost sort the response carries every comparison row in the same
    // ordering, so there are more rows than items. Paginating on the item
    // count would strand the rows past the first page with no way to reach
    // them -- the browser would render one page and hide the Next button.
    getStock.mockResolvedValue({ total: 2, row_total: 500, page: 1, per_page: 250, items })
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())

    expect(screen.getByText('Page 1 of 2')).toBeTruthy()

    fireEvent.click(screen.getByText('Next →'))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ page: 2 })))
  })

  it('asks for comparison rows in list view and not in tile view', async () => {
    // Tiles drop comparison rows on the floor, and under a Cost sort a page of
    // the flattened set is mostly comparison rows -- fetching them would leave
    // the grid near-empty.
    render(<StockBrowser />)
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ includeComparisons: true })))

    fireEvent.click(screen.getByTitle('Tile view'))
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ includeComparisons: false })))

    fireEvent.click(screen.getByTitle('List view'))
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ includeComparisons: true })))
  })

  it('returns to page 1 when the view mode changes', async () => {
    // List pages over the flattened offer rows under a Cost sort, tiles only
    // over the items, so a page deep in one can be past the end of the other.
    getStock.mockResolvedValue({ total: 2, row_total: 500, page: 1, per_page: 250, items })
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByText('Next →'))
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 })))

    fireEvent.click(screen.getByTitle('Tile view'))
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 1, includeComparisons: false }),
    ))
  })

  it('renders a row for every item, including comparison rows, in list view', async () => {
    getStock.mockResolvedValue({
      total: 1, row_total: 1, page: 1, per_page: 250,
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

  it('shows the name the source gave a match in place of the target title, keeping the target title as hover text', async () => {
    getStock.mockResolvedValue({
      total: 1, row_total: 1, page: 1, per_page: 250,
      items: [
        items[0],
        { id: 'k1:Amazon', item_key: 'k1', is_own: false, artist: 'Rob Zombie', title: 'The Great Satan — Ghostly Black Vinyl', listing_title: 'Rob Zombie - The Great Satan [Standard Black LP]', format: 'Vinyl', price: 29.99, currency: 'USD', url: 'https://amazon/x', cover_image_url: null, source: 'Amazon', last_seen: '2026-07-05T00:00:00Z', reason: null },
      ],
    })
    render(<StockBrowser />)
    const found = await screen.findByText('Rob Zombie - The Great Satan [Standard Black LP]')
    expect(found.getAttribute('title')).toBe('The Great Satan — Ghostly Black Vinyl')
    // The own row, which reported no name of its own, still shows the target title.
    expect(screen.getAllByText('The Great Satan — Ghostly Black Vinyl').length).toBe(1)
  })

  it('shows only the own row per item in tile view, even when comparison rows are present', async () => {
    getStock.mockResolvedValue({
      total: 1, row_total: 1, page: 1, per_page: 250,
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
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.getAllByTitle('Save for later').length).toBeGreaterThanOrEqual(1)
  })

  it('clicking the bookmark button calls saveStockItem with the item_key and flips the icon title', async () => {
    saveStockItem.mockResolvedValue({ saved: true })
    render(<StockBrowser />)
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
    getStock.mockResolvedValueOnce({ total: 2, row_total: 2, page: 1, per_page: 250, items })
    getStock.mockResolvedValue({
      total: 2, row_total: 2, page: 1, per_page: 250,
      items: [{ ...items[0], saved: true }, items[1]],
    })
    render(<StockBrowser />)
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
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByTitle('Tile view'))
    await waitFor(() => expect(screen.getAllByTitle('Save for later').length).toBeGreaterThanOrEqual(1))
  })

  it('clicking the tile bookmark button calls saveStockItem and prevents the enclosing tile link from navigating', async () => {
    saveStockItem.mockResolvedValue({ saved: true })
    render(<StockBrowser />)
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
      total: 1, row_total: 1, page: 1, per_page: 250,
      items: [{ ...items[0], saved: true }],
    })
    unsaveStockItem.mockResolvedValue({ saved: false })
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    chooseFilter('saved')
    const button = await screen.findByTitle('Remove from saved')
    fireEvent.click(button)
    await waitFor(() => expect(screen.queryByText('The Great Satan — Ghostly Black Vinyl')).toBeNull())
  })

  it('re-fetches from the server when a toggle fails, undoing the optimistic removal under the Saved filter', async () => {
    unsaveStockItem.mockRejectedValue(new Error('boom'))
    getStock.mockResolvedValue({
      total: 1, row_total: 1, page: 1, per_page: 250,
      items: [{ ...items[0], saved: true }],
    })
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    chooseFilter('saved')
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
      total: 1, row_total: 1, page: 1, per_page: 250,
      items: [{ ...items[0], saved: true }],
    })
    unsaveStockItem.mockResolvedValue({ saved: false })
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    chooseFilter('saved')
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
        return Promise.resolve({ total: 2, row_total: 2, page: 1, per_page: 250, items: bothItems })
      }
      if (callCount === 2) {
        // The retry load triggered by the failed toggle.
        return new Promise((resolve) => { resolveRetryCall = resolve })
      }
      // The load triggered by the search box change that follows.
      return new Promise((resolve) => { resolveSearchCall = resolve })
    })

    render(<StockBrowser />)
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
    resolveSearchCall({ total: 1, row_total: 1, page: 1, per_page: 250, items: [items[1]] })
    await waitFor(() => expect(screen.getByText('Every Bridge Burning — Forest Green LP')).toBeTruthy())
    expect(screen.queryByText('The Great Satan — Ghostly Black Vinyl')).toBeNull()

    resolveRetryCall({ total: 2, row_total: 2, page: 1, per_page: 250, items: bothItems })
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

describe('StockBrowser Cheapest filter', () => {
  it('renders an unchecked Cheapest checkbox on Store and fetches without it', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(getStock).toHaveBeenCalled())
    const box = cheapestBox()
    expect(box.checked).toBe(false)
    expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ cheapest: false }))
  })

  it('stacks Cheapest on a library filter', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(getStock).toHaveBeenCalled())
    chooseFilter('collection')
    fireEvent.click(cheapestBox())
    await waitFor(() => expect(getStock).toHaveBeenLastCalledWith(expect.objectContaining({
      libraryScope: 'collection', cheapest: true,
    })))
  })

  it('ticking Cheapest refetches from page 1 with cheapest set, and stacks on the current filter', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(getStock).toHaveBeenCalled())
    chooseFilter('saved')
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ saved: true })))
    fireEvent.click(cheapestBox())
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({
      cheapest: true, saved: true, page: 1,
    })))
    // The sidebar never takes the flag: every record keeps at least one row,
    // so no artist can drop out of it.
    expect(getStockArtists).not.toHaveBeenCalledWith(expect.objectContaining({ cheapest: true }))
  })

  it('persists the Cheapest choice and restores it on mount', async () => {
    const { unmount } = render(<StockBrowser />)
    await waitFor(() => expect(getStock).toHaveBeenCalled())
    fireEvent.click(cheapestBox())
    await waitFor(() => expect(localStorage.getItem('stockCheapest')).toBe('true'))
    unmount()

    getStock.mockClear()
    render(<StockBrowser />)
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ cheapest: true })))
    expect(cheapestBox().checked).toBe(true)
  })

  it('passes Cheapest through to the Stats panel', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(getStock).toHaveBeenCalled())
    fireEvent.click(cheapestBox())
    fireEvent.click(screen.getByRole('button', { name: 'Stats' }))
    await waitFor(() => expect(getStockStats).toHaveBeenCalledWith(expect.objectContaining({ cheapest: true })))
  })
})

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

  it('offers Stats beside Source', async () => {
    render(<StockBrowser crawlers={CRAWLERS} />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    const source = screen.getByRole('button', { name: 'Source' })
    const stats = screen.getByRole('button', { name: 'Stats' })
    // Each sits in its own anchor wrapper; Stats' wrapper follows Source's.
    expect(source.parentElement!.nextElementSibling).toBe(stats.parentElement)
  })

  it('omits the toolbar item count, since Stats already shows the total', async () => {
    render(<StockBrowser crawlers={CRAWLERS} />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.queryByText(/^2 items$/)).toBeNull()
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

    chooseFilter('recommended')
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
    chooseFilter('saved')

    fireEvent.click(screen.getByRole('button', { name: 'Stats' }))
    await waitFor(() => expect(getStockStats).toHaveBeenCalled())
    expect(getStockStats).toHaveBeenLastCalledWith(expect.objectContaining({
      search: 'zombie', saved: true, recommended: false, overlapped: false, hiddenCrawlerIds: [5],
    }))
    expect(await screen.findByRole('img', { name: /items by source/i })).toBeInTheDocument()
  })
})
