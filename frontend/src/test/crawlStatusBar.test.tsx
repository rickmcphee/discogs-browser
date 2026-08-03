import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from '../App'
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

const { release } = vi.hoisted(() => ({
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
    listings: {},
  } as Release,
}))

vi.mock('../api/client', () => ({
  checkHealth: vi.fn().mockResolvedValue(true),
  getAuthStatus: vi.fn().mockResolvedValue({ state: 'authenticated', user: { discogs_username: 'test', is_admin: true } }),
  setUnauthorizedHandler: vi.fn(),
  refreshCollection: vi.fn().mockResolvedValue({ synced: 0, username: 'test' }),
  getCollectionStatus: vi.fn().mockResolvedValue({ total: 0, last_synced: null }),
  getCrawlStatus: vi.fn().mockResolvedValue({ total: 0, missing: 0, oldest_checked: null }),
  postCrawlStart: vi.fn().mockResolvedValue({ started: true, running: true }),
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
}))

function getLastCrawlSource() {
  return MockEventSource.instances[MockEventSource.instances.length - 1]
}

beforeEach(() => {
  MockEventSource.instances = []
  vi.clearAllMocks()
  localStorage.clear()
})

async function clickRefreshAndGetSource() {
  const [button] = await screen.findAllByTitle('Refresh prices for this record')
  fireEvent.click(button)
  await waitFor(() => expect(getLastCrawlSource()).toBeDefined())
  return getLastCrawlSource()
}

describe('crawl status bar', () => {
  it('shows "Refreshing prices…" after a per-row refresh is clicked', async () => {
    render(<App />)
    const src = await clickRefreshAndGetSource()
    src.emit({ status: 'started', total: 1, id: 1 })
    await waitFor(() =>
      expect(screen.getByText(/Refreshing prices/i)).toBeInTheDocument()
    )
  })

  it('shows artist, title, and site from the current crawl event', async () => {
    render(<App />)
    const src = await clickRefreshAndGetSource()
    src.emit({ status: 'started', total: 2, id: 1 })
    src.emit({ status: 'found', discogs_id: 'r1', release: 'The Wall', artist: 'Pink Floyd', site: 'Amazon', price: 24.99 })

    await waitFor(() => expect(screen.getByText('Pink Floyd — The Wall')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText('Amazon')).toBeInTheDocument())
  })

  it('shows X/total progress count', async () => {
    render(<App />)
    const src = await clickRefreshAndGetSource()
    src.emit({ status: 'started', total: 4, id: 1 })
    src.emit({ status: 'not_found', discogs_id: 'r1', release: 'Wish You Were Here', artist: 'Pink Floyd', site: 'Amazon' })

    await waitFor(() => expect(screen.getByText(/1\/4/)).toBeInTheDocument())
  })

  it('shows Done and Dismiss when complete', async () => {
    render(<App />)
    const src = await clickRefreshAndGetSource()
    src.emit({ status: 'started', total: 1, id: 1 })
    src.emit({ status: 'complete', id: 2 })

    await waitFor(() => expect(screen.getByText('Done')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /Dismiss/i })).toBeInTheDocument()
  })

  it('hides the status bar after Dismiss', async () => {
    render(<App />)
    const src = await clickRefreshAndGetSource()
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

  it('does not resurrect a dismissed banner when a refresh replays the same buffered events', async () => {
    const { unmount } = render(<App />)
    const src = await clickRefreshAndGetSource()
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

    await waitFor(() => expect(screen.getByText('Collection')).toBeInTheDocument())
    expect(screen.queryByText('Done')).not.toBeInTheDocument()
  })
})
