import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from '../App'
import { importRecommendationsCsv } from '../api/client'
import type { Release } from '../api/types'

class MockEventSource {
  static instances: MockEventSource[] = []
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  constructor() { MockEventSource.instances.push(this) }
  emit(data: object) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent)
  }
}

const { release, postCrawlStart } = vi.hoisted(() => ({
  postCrawlStart: vi.fn(),
  release: {
    discogs_id: 'r1',
    artist: 'Pink Floyd',
    title: 'The Wall',
    year: 1979,
    label: 'Harvest',
    format: 'Vinyl',
    discogs_price: null,
    cover_image_url: '',
    discogs_url: '',
    plex_url: null,
    plex_matched_at: null,
    last_synced: '',
    date_added: null,
  } as Release,
}))

vi.mock('../api/client', () => ({
  checkHealth: vi.fn().mockResolvedValue(true),
  getAuthStatus: vi.fn().mockResolvedValue({ state: 'authenticated', user: { discogs_username: 'test', is_admin: true } }),
  setUnauthorizedHandler: vi.fn(),
  getUserHiddenCrawlers: vi.fn().mockResolvedValue([]),
  postUserHiddenCrawlers: vi.fn().mockResolvedValue(undefined),
  refreshCollection: vi.fn().mockResolvedValue({ synced: 0, username: 'test' }),
  getCollectionStatus: vi.fn().mockResolvedValue({ total: 0, last_synced: null }),
  getCrawlStatus: vi.fn().mockResolvedValue({ total: 0, missing: 0, oldest_checked: null }),
  postCrawlStart: (...args: unknown[]) => postCrawlStart(...args),
  getCrawlers: vi.fn().mockResolvedValue([]),
  openCrawlStream: vi.fn(() => new MockEventSource()),
  getReleases: vi.fn().mockResolvedValue({ total: 1, page: 1, per_page: 50, releases: [release] }),
  getArtists: vi.fn().mockResolvedValue(['Pink Floyd']),
  getSettings: vi.fn().mockResolvedValue({
    crawl_delay_seconds: 30, consecutive_failure_limit: 10, crawl_schedule: '',
    crawl_schedule_mode: 'missing',
    ebay_app_id: '', ebay_cert_id: '', stock_schedule: '',
  }),
  getUserSettings: vi.fn().mockResolvedValue({ anthropic_api_key: '', recommendation_item_limit: 300, plex_base_url: '', plex_token: '', plex_match_threshold: 90 }),
  saveSettings: vi.fn(),
  saveUserSettings: vi.fn(),
  setCrawlerEnabled: vi.fn(),
  logout: vi.fn(),
  hasAvatar: vi.fn().mockResolvedValue(false),
  uploadAvatar: vi.fn(),
  deleteAvatar: vi.fn(),
  avatarUrl: vi.fn((v: number) => `/api/auth/avatar?v=${v}`),
  openLogsStream: vi.fn(() => new MockEventSource()),
  screenshotUrl: vi.fn((path: string) => `/api/screenshots/${path}`),
  clearLogs: vi.fn(),
  getStock: vi.fn().mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] }),
  getStockArtists: vi.fn().mockResolvedValue([]),
  postStockSyncStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  postJudgmentStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  getJudgmentStatus: vi.fn().mockResolvedValue({ any_judged: false }),
  getPriceStatus: vi.fn().mockResolvedValue({ any_price_paid: false }),
  importRecommendationsCsv: vi.fn(),
  listInvites: vi.fn().mockResolvedValue([]),
  createInvite: vi.fn().mockResolvedValue({ code: '' }),
}))

function getLastCrawlSource() {
  return MockEventSource.instances[MockEventSource.instances.length - 1]
}

beforeEach(() => {
  MockEventSource.instances = []
  vi.clearAllMocks()
  localStorage.clear()
  postCrawlStart.mockResolvedValue({ enqueued: 0 })
})

async function clickMarketplaceRefresh() {
  render(<App />)
  await waitFor(() => expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument())
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
  const description = await screen.findByText('Run price crawlers immediately.')
  const button = (description.closest('tr') as HTMLElement).querySelector('button') as HTMLButtonElement
  fireEvent.click(button)
  return button
}

async function getCrawlSourceOnMount() {
  await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
  return getLastCrawlSource()
}

describe('crawl status bar', () => {
  it('shows "Refreshing prices…" once a crawl starts', async () => {
    render(<App />)
    const src = await getCrawlSourceOnMount()
    src.emit({ status: 'started', total: 1, id: 1 })
    await waitFor(() =>
      expect(screen.getByText(/Refreshing prices/i)).toBeInTheDocument()
    )
  })

  it('shows artist, title, and site from the current crawl event', async () => {
    render(<App />)
    const src = await getCrawlSourceOnMount()
    src.emit({ status: 'started', total: 2, id: 1 })
    src.emit({ status: 'found', discogs_id: 'r1', release: 'The Wall', artist: 'Pink Floyd', site: 'Amazon', price: 24.99 })

    await waitFor(() => expect(screen.getByText('Pink Floyd — The Wall')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText('Amazon')).toBeInTheDocument())
  })

  it('shows X/total progress count', async () => {
    render(<App />)
    const src = await getCrawlSourceOnMount()
    src.emit({ status: 'started', total: 4, id: 1 })
    src.emit({ status: 'not_found', discogs_id: 'r1', release: 'Wish You Were Here', artist: 'Pink Floyd', site: 'Amazon' })

    await waitFor(() => expect(screen.getByText(/1\/4/)).toBeInTheDocument())
  })

  it('shows Done and Dismiss when complete', async () => {
    render(<App />)
    const src = await getCrawlSourceOnMount()
    src.emit({ status: 'started', total: 1, id: 1 })
    src.emit({ status: 'complete', id: 2 })

    await waitFor(() => expect(screen.getByText('Done')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /Dismiss/i })).toBeInTheDocument()
  })

  it('hides the status bar after Dismiss', async () => {
    render(<App />)
    const src = await getCrawlSourceOnMount()
    src.emit({ status: 'started', total: 1, id: 1 })
    src.emit({ status: 'complete', id: 2 })

    await waitFor(() => screen.getByRole('button', { name: /Dismiss/i }))
    fireEvent.click(screen.getByRole('button', { name: /Dismiss/i }))

    expect(screen.queryByText('Done')).not.toBeInTheDocument()
  })

  it('shows page/count as soon as a page is fetched, before that page finishes processing', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'sync_started', id: 1 })
    await waitFor(() => expect(screen.getByText('Syncing collection…')).toBeInTheDocument())

    source.emit({ status: 'sync_page_fetched', page: 1, total_pages: 12, page_count: 100, id: 2 })
    await waitFor(() =>
      expect(screen.getByText('Syncing collection… 100 records (page 1/12)')).toBeInTheDocument()
    )
  })

  it('still shows the processed running total once a page finishes processing', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'sync_page_fetched', page: 1, total_pages: 12, page_count: 100, id: 1 })
    source.emit({ status: 'sync_progress', synced: 100, page: 1, total_pages: 12, id: 2 })
    await waitFor(() =>
      expect(screen.getByText('Syncing collection… 100 records (page 1/12)')).toBeInTheDocument()
    )
  })

  it('says "wantlist" for a wantlist-scoped sync, though the event says wishlist', async () => {
    render(<App />)
    const source = await getCrawlSourceOnMount()
    source.emit({ status: 'sync_started', scope: 'wishlist', id: 1 })
    await waitFor(() => expect(screen.getByText('Syncing wantlist…')).toBeInTheDocument())

    source.emit({ status: 'sync_complete', scope: 'wishlist', wishlist_synced: 7, username: 'alice', id: 2 })
    await waitFor(() => expect(screen.getByText('Synced 7 wantlist items for alice')).toBeInTheDocument())
  })

  it('appends the wantlist count to a full collection sync', async () => {
    render(<App />)
    const source = await getCrawlSourceOnMount()
    source.emit({ status: 'sync_complete', synced: 42, wishlist_synced: 7, username: 'alice', id: 1 })
    await waitFor(() =>
      expect(screen.getByText('Synced 42 records for alice, 7 wantlist items')).toBeInTheDocument()
    )
  })

  it('does not resurrect a dismissed banner when a refresh replays the same buffered events', async () => {
    const { unmount } = render(<App />)
    const src = await getCrawlSourceOnMount()
    src.emit({ status: 'started', total: 1, id: 1 })
    src.emit({ status: 'complete', id: 2 })

    await waitFor(() => screen.getByRole('button', { name: /Dismiss/i }))
    fireEvent.click(screen.getByRole('button', { name: /Dismiss/i }))
    unmount()

    // A browser refresh remounts the app and opens a fresh SSE connection, which
    // replays every buffered event — including the one just dismissed.
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const replaySrc = MockEventSource.instances[MockEventSource.instances.length - 1]
    replaySrc.emit({ status: 'started', total: 1, id: 1 })
    replaySrc.emit({ status: 'complete', id: 2 })

    await waitFor(() => expect(screen.getByRole('button', { name: 'Collection' })).toBeInTheDocument())
    expect(screen.queryByText('Done')).not.toBeInTheDocument()
  })
})

// POST /crawl/start only enqueues, and the worker pool broadcasts no lifecycle
// event when it later picks the work up, so between the click and the first
// listing_changed there was nothing at all on screen. The reply's own count is
// the only confirmation that exists at click time -- and it counts targets
// requested, not rows inserted, which is what the wording has to say.
describe('price refresh feedback', () => {
  it('reports how many records the refresh was requested for', async () => {
    postCrawlStart.mockResolvedValue({ enqueued: 412 })
    await clickMarketplaceRefresh()
    await waitFor(() =>
      expect(screen.getByText('Price refresh requested for 412 records.')).toBeInTheDocument()
    )
  })

  it('says "1 record", not "1 records"', async () => {
    postCrawlStart.mockResolvedValue({ enqueued: 1 })
    await clickMarketplaceRefresh()
    await waitFor(() =>
      expect(screen.getByText('Price refresh requested for 1 record.')).toBeInTheDocument()
    )
  })

  // Zero targets is a successful click with nothing to do, which reads exactly
  // like a dead button unless it says so.
  it('says so when a missing-only refresh finds nothing to queue', async () => {
    postCrawlStart.mockResolvedValue({ enqueued: 0 })
    await clickMarketplaceRefresh()
    await waitFor(() =>
      expect(screen.getByText('Nothing to refresh — every record already has a price.')).toBeInTheDocument()
    )
    expect(screen.getByRole('button', { name: /Dismiss/i })).toBeInTheDocument()
  })

  it('spins the button and says what it is doing while the request is in flight', async () => {
    let resolve: (value: { enqueued: number }) => void = () => {}
    postCrawlStart.mockReturnValue(new Promise((r) => { resolve = r }))
    const button = await clickMarketplaceRefresh()

    await waitFor(() =>
      expect(screen.getByText('Starting price refresh for records with no price yet…')).toBeInTheDocument()
    )
    expect(button).toBeDisabled()
    expect(button.querySelector('.animate-spin')).toBeInTheDocument()

    resolve({ enqueued: 3 })
    await waitFor(() => expect(button).not.toBeDisabled())
    expect(button).toHaveTextContent('Refresh')
  })

  // The spinner has to describe the message on screen, not "is anything
  // pending anywhere": a stock sync completing behind a still-in-flight price
  // request used to leave its completion message spinning with no Dismiss.
  it('does not spin a finished message just because another request is in flight', async () => {
    let resolve: (value: { enqueued: number }) => void = () => {}
    postCrawlStart.mockReturnValue(new Promise((r) => { resolve = r }))
    await clickMarketplaceRefresh()
    await waitFor(() =>
      expect(screen.getByText('Starting price refresh for records with no price yet…')).toBeInTheDocument()
    )
    expect(screen.queryByRole('button', { name: /Dismiss/i })).not.toBeInTheDocument()

    // A stock sync finishes while the price request is still unanswered.
    getLastCrawlSource().emit({ status: 'stock_sync_complete', synced: 12, id: 1 })
    await waitFor(() => expect(screen.getByText(/In-stock sync complete: 12 items/)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /Dismiss/i })).toBeInTheDocument()

    resolve({ enqueued: 3 })
  })

  it('reports a failed start in the status bar instead of a blocking alert', async () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})
    postCrawlStart.mockRejectedValue(new Error('network down'))
    await clickMarketplaceRefresh()
    await waitFor(() =>
      expect(screen.getByText('Price refresh failed to start: network down')).toBeInTheDocument()
    )
    expect(alertSpy).not.toHaveBeenCalled()
    alertSpy.mockRestore()
  })
})

describe('recommendations import', () => {
  async function importFile() {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Profile' }))
    const file = new File(['artist,title\n'], 'recommendations.csv', { type: 'text/csv' })
    const input = screen.getByTestId('recommendations-import-input') as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })
  }

  it('shows a clean error message (not raw JSON) when the import fails', async () => {
    vi.mocked(importRecommendationsCsv).mockRejectedValueOnce(
      new Error(JSON.stringify({ detail: 'Missing required column(s): item_key.' }))
    )
    await importFile()

    await waitFor(() =>
      expect(screen.getByText('Import recommendations failed: Missing required column(s): item_key.')).toBeInTheDocument()
    )
    expect(screen.queryByText(/"detail"/)).not.toBeInTheDocument()
  })

  it('says nothing new to import when a re-import finds every row already current', async () => {
    vi.mocked(importRecommendationsCsv).mockResolvedValueOnce({
      imported: 0, updated: 0, unchanged: 1, skipped: 0, errors: [], matched_stock_items: 0, running: false,
    })
    await importFile()
    await waitFor(() =>
      expect(screen.getByText('Nothing new to import — 1 judgment already up to date.')).toBeInTheDocument()
    )
  })

  it('reports a partial stock match and skipped rows on a large import', async () => {
    vi.mocked(importRecommendationsCsv).mockResolvedValueOnce({
      imported: 1200, updated: 0, unchanged: 0, skipped: 3, errors: [], matched_stock_items: 87, running: false,
    })
    await importFile()
    await waitFor(() =>
      expect(
        screen.getByText('Imported 1200 judgments. 87 in stock now; the rest apply as items appear. 3 rows skipped.')
      ).toBeInTheDocument()
    )
  })

  it('says none are in stock yet when nothing imported matches a current stock item', async () => {
    vi.mocked(importRecommendationsCsv).mockResolvedValueOnce({
      imported: 1200, updated: 0, unchanged: 0, skipped: 0, errors: [], matched_stock_items: 0, running: false,
    })
    await importFile()
    await waitFor(() =>
      expect(
        screen.getByText('Imported 1200 judgments. None in stock yet — they apply as items appear.')
      ).toBeInTheDocument()
    )
  })

  it('omits the "rest apply" clause when every imported item is already in stock', async () => {
    vi.mocked(importRecommendationsCsv).mockResolvedValueOnce({
      imported: 1, updated: 0, unchanged: 0, skipped: 0, errors: [], matched_stock_items: 1, running: false,
    })
    await importFile()
    await waitFor(() =>
      expect(screen.getByText('Imported 1 judgment. 1 in stock now.')).toBeInTheDocument()
    )
  })
})
