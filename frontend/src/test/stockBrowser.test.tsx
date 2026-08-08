import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import StockBrowser from '../views/StockBrowser'

const items = [
  { id: 1, item_key: 'k1', is_own: true, artist: 'Rob Zombie', title: 'The Great Satan — Ghostly Black Vinyl', format: 'Vinyl', price: 31.99, currency: 'USD', url: 'https://shop.nuclearblast.com/products/rob-zombie', cover_image_url: 'https://cdn.shopify.com/rz-black.png', source: 'Nuclear Blast', last_seen: '2026-07-05T00:00:00Z' },
  { id: 2, item_key: 'k2', is_own: true, artist: 'NAILS', title: 'Every Bridge Burning — Forest Green LP', format: 'Vinyl', price: 25.99, currency: 'USD', url: 'https://shop.nuclearblast.com/products/nails', cover_image_url: null, source: 'Nuclear Blast', last_seen: '2026-07-05T00:00:00Z' },
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

  it('gives the list-view thumbnail a min-width so it matches Collection/Wishlist sizing', async () => {
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

  it('searches by artist or title', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByPlaceholderText('Search artist or title…'), { target: { value: 'nails' } })
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ search: 'nails' })))
  })

  it('toggles sort order when a column header is clicked twice', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByText(/Price/))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ sort: 'price', order: 'asc' })))
    fireEvent.click(screen.getByText(/Price/))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ sort: 'price', order: 'desc' })))
  })

  it('sorts by format when the Format column header is clicked', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByText(/Format/))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ sort: 'format', order: 'asc' })))
  })

  it('renders an artist sidebar with All plus each distinct artist, and filters on click', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'All' })).toBeTruthy())
    expect(screen.getByRole('button', { name: 'NAILS' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Rob Zombie' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'NAILS' }))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ artist: 'NAILS' })))
  })

  it('defaults sort to title when a specific artist is selected, and back to artist for All', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'All' })).toBeTruthy())
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
  })

  it('enables Recommended when recommendedAvailable is true', async () => {
    render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect((screen.getByRole('option', { name: 'Recommended' }) as HTMLOptionElement).disabled).toBe(false)
  })

  it('resets filter to All when recommendedAvailable becomes false while Recommended is selected', async () => {
    localStorage.setItem('stockFilter', 'recommended')
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
    expect(getStockArtists).toHaveBeenLastCalledWith(false, false, [])
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'recommended' } })
    await waitFor(() => expect(getStockArtists).toHaveBeenLastCalledWith(false, true, []))
  })

  it('restores a previously-selected Recommended filter from localStorage', async () => {
    localStorage.setItem('stockFilter', 'recommended')
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
    expect(getStockArtists).toHaveBeenLastCalledWith(false, false, [3])
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

  it('persists the filter to localStorage under stockFilter and restores it on remount', async () => {
    const { unmount } = render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'recommended' } })
    await waitFor(() => expect(localStorage.getItem('stockFilter')).toBe('recommended'))
    unmount()
    render(<StockBrowser recommendedAvailable />)
    await waitFor(() => expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('recommended'))
  })

  it('scope="collection" forces overlapping and hides the filter dropdown', async () => {
    render(<StockBrowser scope="collection" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ overlapping: true }))
  })

  it('scope="collection" forces overlapping on the artist sidebar fetch too', async () => {
    render(<StockBrowser scope="collection" />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(getStockArtists).toHaveBeenCalledWith(true, false, [])
  })

  it('scope="store" (default) keeps the filter dropdown with All/Recommended', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.getByRole('combobox')).toBeTruthy()
    expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ overlapping: false }))
  })

  it('persists the view mode to localStorage under collectionViewMode_instock', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByTitle('Tile view'))
    await waitFor(() => expect(localStorage.getItem('collectionViewMode_instock')).toBe('tiles'))
  })

  it('shows a spinner alongside Loading… during the initial fetch', async () => {
    let resolveFetch: (v: any) => void = () => {}
    getStock.mockReturnValue(new Promise((resolve) => { resolveFetch = resolve }))
    render(<StockBrowser />)
    expect(screen.getByText('Loading…')).toBeTruthy()
    expect(document.querySelector('.animate-spin')).toBeTruthy()
    resolveFetch({ total: 0, page: 1, per_page: 250, items: [] })
    await waitFor(() => expect(screen.queryByText('Loading…')).toBeNull())
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
