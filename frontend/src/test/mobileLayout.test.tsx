import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, renderHook, screen, fireEvent, waitFor, within } from '@testing-library/react'
import App from '../App'
import RecordBrowser from '../views/RecordBrowser'
import StockBrowser from '../views/StockBrowser'
import { useIsMobile } from '../hooks/useMediaQuery'

class MockEventSource {
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
}

const { getAuthStatus, getReleases, getArtists, getStock, getStockArtists, saveStockItem, openLogsStream } = vi.hoisted(() => ({
  getAuthStatus: vi.fn(),
  getReleases: vi.fn(),
  getArtists: vi.fn(),
  getStock: vi.fn(),
  getStockArtists: vi.fn(),
  saveStockItem: vi.fn(),
  openLogsStream: vi.fn(),
}))

vi.mock('../api/client', () => ({
  checkHealth: vi.fn().mockResolvedValue(true),
  getAuthStatus,
  setUnauthorizedHandler: vi.fn(),
  getUserHiddenCrawlers: vi.fn().mockResolvedValue([]),
  postUserHiddenCrawlers: vi.fn().mockResolvedValue(undefined),
  refreshCollection: vi.fn().mockResolvedValue({ synced: 0, username: 'test' }),
  getCollectionStatus: vi.fn().mockResolvedValue({ total: 0, last_synced: null }),
  getCrawlStatus: vi.fn().mockResolvedValue({ total: 0, missing: 0, oldest_checked: null }),
  postCrawlStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  getCrawlers: vi.fn().mockResolvedValue([]),
  openCrawlStream: vi.fn(() => new MockEventSource()),
  getReleases,
  getArtists,
  getSettings: vi.fn().mockResolvedValue({
    crawl_delay_seconds: 30, consecutive_failure_limit: 10, crawl_schedule: '',
    crawl_schedule_mode: 'missing', ebay_app_id: '', ebay_cert_id: '', stock_schedule: '',
  }),
  getUserSettings: vi.fn().mockResolvedValue({ anthropic_api_key: '', recommendation_item_limit: 300, plex_base_url: '', plex_token: '', plex_match_threshold: 90 }),
  saveUserSettings: vi.fn(),
  saveSettings: vi.fn(),
  setCrawlerEnabled: vi.fn(),
  logout: vi.fn(),
  hasAvatar: vi.fn().mockResolvedValue(false),
  uploadAvatar: vi.fn(),
  deleteAvatar: vi.fn(),
  avatarUrl: vi.fn((v: number) => `/api/auth/avatar?v=${v}`),
  openLogsStream,
  screenshotUrl: vi.fn((path: string) => `/api/screenshots/${path}`),
  clearLogs: vi.fn(),
  getStock,
  getStockArtists,
  saveStockItem,
  unsaveStockItem: vi.fn().mockResolvedValue(undefined),
  postStockSyncStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  postJudgmentStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  clearJudgments: vi.fn(),
  exportRecommendationsCsv: vi.fn(),
  importRecommendationsCsv: vi.fn(),
  getJudgmentStatus: vi.fn().mockResolvedValue({ any_judged: false }),
  getPriceStatus: vi.fn().mockResolvedValue({ any_price_paid: false }),
  listInvites: vi.fn().mockResolvedValue([]),
  createInvite: vi.fn().mockResolvedValue({ code: '' }),
  getQueueSummary: vi.fn().mockResolvedValue(null),
  getQueueNext: vi.fn().mockResolvedValue([]),
}))

const PHONE_WIDTH = 390
const DESKTOP_WIDTH = 1280
const defaultMatchMedia = window.matchMedia

// The hook only ever asks a max-width question, so honouring that one form is
// enough to put a render on either side of the breakpoint.
function setViewportWidth(width: number) {
  window.matchMedia = ((query: string) => {
    const max = /max-width:\s*(\d+)px/.exec(query)
    return {
      matches: max ? width <= Number(max[1]) : false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    } as MediaQueryList
  }) as typeof window.matchMedia
}

const release = {
  discogs_id: 'r1', artist: 'Pink Floyd', title: 'The Wall', year: 1979, label: 'Harvest',
  format: 'Vinyl', discogs_price: null, cover_image_url: 'https://x/cover.jpg',
  discogs_url: 'https://discogs.com/r1', plex_url: null, plex_matched_at: null,
  last_synced: '', date_added: null,
}

const stockItem = {
  id: 1, item_key: 'k1', is_own: true, artist: 'Rob Zombie', title: 'The Great Satan',
  format: 'Vinyl', price: 31.99, currency: 'USD', url: 'https://shop.example/rz',
  cover_image_url: null, source: 'Nuclear Blast', last_seen: '2026-07-05T00:00:00Z',
  discogs_price: null, saved: false,
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  openLogsStream.mockImplementation(() => new MockEventSource())
  getAuthStatus.mockResolvedValue({ state: 'authenticated', user: { discogs_username: 'test', is_admin: true } })
  getReleases.mockResolvedValue({ total: 0, page: 1, per_page: 250, releases: [] })
  getArtists.mockResolvedValue([])
  getStock.mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] })
  getStockArtists.mockResolvedValue([])
  saveStockItem.mockResolvedValue(undefined)
  setViewportWidth(PHONE_WIDTH)
})

afterEach(() => {
  window.matchMedia = defaultMatchMedia
})

describe('useIsMobile', () => {
  it('falls back to the desktop layout when the browser has no matchMedia', () => {
    // Restored by afterEach; the fallback is what keeps every other test file
    // rendering the desktop tree it was written against.
    ;(window as unknown as { matchMedia: undefined }).matchMedia = undefined
    const { result } = renderHook(() => useIsMobile())
    expect(result.current).toBe(false)
  })

  it('reports mobile below the breakpoint and desktop at it', () => {
    setViewportWidth(767)
    expect(renderHook(() => useIsMobile()).result.current).toBe(true)
    setViewportWidth(768)
    expect(renderHook(() => useIsMobile()).result.current).toBe(false)
  })
})

describe('mobile app shell', () => {
  it('moves the library tabs into a bottom bar rather than duplicating them', async () => {
    render(<App />)
    const bar = await screen.findByRole('navigation', { name: 'Sections' })
    for (const label of ['Collection', 'Wantlist', 'Store', 'Track']) {
      expect(within(bar).getByRole('button', { name: label })).toBeInTheDocument()
      // One button per tab in the whole document -- a second, hidden copy in
      // the header would be announced by a screen reader and matched by find.
      expect(screen.getAllByRole('button', { name: label })).toHaveLength(1)
    }
  })

  it('marks the tab the app is actually on, and follows a tap to another', async () => {
    render(<App />)
    const bar = await screen.findByRole('navigation', { name: 'Sections' })
    expect(within(bar).getByRole('button', { name: 'Collection' })).toHaveAttribute('aria-current', 'page')

    fireEvent.click(within(bar).getByRole('button', { name: 'Store' }))
    await waitFor(() =>
      expect(within(bar).getByRole('button', { name: 'Store' })).toHaveAttribute('aria-current', 'page')
    )
    expect(within(bar).getByRole('button', { name: 'Collection' })).not.toHaveAttribute('aria-current')
  })

  it('keeps the desktop header nav at desktop widths', async () => {
    setViewportWidth(DESKTOP_WIDTH)
    render(<App />)
    await screen.findByRole('button', { name: 'Collection' })
    expect(screen.queryByRole('navigation', { name: 'Sections' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument()
  })

  it('reaches the admin tabs through the header overflow menu', async () => {
    render(<App />)
    const more = await screen.findByRole('button', { name: 'More' })
    // The admin tabs are behind the menu, not merely restyled, so nothing
    // named Settings exists until it is opened.
    expect(screen.queryByRole('button', { name: 'Settings' })).not.toBeInTheDocument()

    fireEvent.click(more)
    const menu = screen.getByRole('dialog', { name: 'Admin sections' })
    fireEvent.click(within(menu).getByRole('button', { name: 'Settings' }))

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Admin sections' })).not.toBeInTheDocument())
    const heading = screen.getByRole('heading', { name: 'Marketplace Management' })
    expect(heading.closest('div.hidden')).toBeNull()
  })

  it('gives a non-admin no overflow menu to open', async () => {
    getAuthStatus.mockResolvedValue({ state: 'authenticated', user: { discogs_username: 'test', is_admin: false } })
    render(<App />)
    await screen.findByRole('navigation', { name: 'Sections' })
    expect(screen.queryByRole('button', { name: 'More' })).not.toBeInTheDocument()
  })
})

describe('mobile RecordBrowser', () => {
  it('renders rows as cards instead of a table, with the columns folded into a meta line', async () => {
    getReleases.mockResolvedValue({ total: 1, page: 1, per_page: 250, releases: [release] })
    render(<RecordBrowser scope="collection" />)
    await screen.findByText('The Wall')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.getByText('Pink Floyd')).toBeInTheDocument()
    expect(screen.getByText('1979 · Harvest · Vinyl')).toBeInTheDocument()
    expect(screen.getByAltText('The Wall').closest('a')).toHaveAttribute('href', 'https://discogs.com/r1')
  })

  it('replaces the artist sidebar with a sheet that applies its selection', async () => {
    getReleases.mockResolvedValue({ total: 1, page: 1, per_page: 250, releases: [release] })
    getArtists.mockResolvedValue(['Pink Floyd'])
    const { container } = render(<RecordBrowser scope="collection" />)
    await screen.findByText('The Wall')
    expect(container.querySelector('aside')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Artist: All' }))
    const sheet = screen.getByRole('dialog', { name: 'Filter by artist' })
    fireEvent.click(within(sheet).getByRole('button', { name: 'Pink Floyd' }))

    await waitFor(() =>
      expect(getReleases).toHaveBeenCalledWith(expect.objectContaining({ artist: 'Pink Floyd' }))
    )
    expect(screen.queryByRole('dialog', { name: 'Filter by artist' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Artist: Pink Floyd' })).toBeInTheDocument()
  })

  it('keeps the sidebar and the table at desktop widths', async () => {
    setViewportWidth(DESKTOP_WIDTH)
    getReleases.mockResolvedValue({ total: 1, page: 1, per_page: 250, releases: [release] })
    const { container } = render(<RecordBrowser scope="collection" />)
    await screen.findByText('The Wall')
    expect(container.querySelector('aside')).not.toBeNull()
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.queryByLabelText('Sort by')).not.toBeInTheDocument()
  })

  it('sorts from the toolbar control the cards leave in place of column headers', async () => {
    getReleases.mockResolvedValue({ total: 1, page: 1, per_page: 250, releases: [release] })
    render(<RecordBrowser scope="collection" />)
    await screen.findByText('The Wall')

    fireEvent.change(screen.getByLabelText('Sort by'), { target: { value: 'year' } })
    await waitFor(() =>
      expect(getReleases).toHaveBeenCalledWith(expect.objectContaining({ sort: 'year', order: 'asc' }))
    )

    fireEvent.click(screen.getByRole('button', { name: 'Sort descending by Year' }))
    await waitFor(() =>
      expect(getReleases).toHaveBeenCalledWith(expect.objectContaining({ sort: 'year', order: 'desc' }))
    )
  })

  it('drops the price sort option when there is no price column to sort by', async () => {
    render(<RecordBrowser scope="collection" hasPriceField={false} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    const options = within(screen.getByLabelText('Sort by')).getAllByRole('option').map((o) => o.textContent)
    expect(options).toContain('By artist')
    expect(options).not.toContain('By price')
  })
})

describe('mobile StockBrowser', () => {
  it('renders cards keeping the cost link and the save button as the row actions', async () => {
    getStock.mockResolvedValue({ total: 1, page: 1, per_page: 250, items: [stockItem] })
    render(<StockBrowser />)
    await screen.findByText('The Great Satan')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.getByText('Vinyl · Nuclear Blast')).toBeInTheDocument()
    expect(screen.getByText('$31.99').closest('a')).toHaveAttribute('href', 'https://shop.example/rz')

    fireEvent.click(screen.getByRole('button', { name: 'Save for later' }))
    await waitFor(() => expect(saveStockItem).toHaveBeenCalledWith('k1'))
  })

  it('labels the discogs price in the meta line, where the Cost link would otherwise be ambiguous', async () => {
    getStock.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      items: [{ ...stockItem, discogs_price: '42.50' }],
    })
    render(<StockBrowser scope="track" />)
    await screen.findByText('The Great Satan')
    expect(screen.getByText('Vinyl · Nuclear Blast · Price 42.50')).toBeInTheDocument()
  })
})
