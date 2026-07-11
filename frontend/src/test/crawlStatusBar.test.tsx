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
    last_synced: '',
    listings: {},
  } as Release,
}))

vi.mock('../api/client', () => ({
  checkHealth: vi.fn().mockResolvedValue(true),
  getAuthState: vi.fn().mockResolvedValue('authenticated'),
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
    discogs_token: '', debug_screenshot_interval: 20, shuffle_crawl_order: true,
    crawl_delay_seconds: 30, consecutive_failure_limit: 10, crawl_schedule: '',
    crawl_schedule_mode: 'missing', collection_schedule: '', collection_schedule_mode: 'all',
    ebay_app_id: '', ebay_cert_id: '', stock_schedule: '', recommendation_item_limit: 300,
  }),
  saveSettings: vi.fn(),
  setCrawlerEnabled: vi.fn(),
  changePassword: vi.fn(),
  logout: vi.fn(),
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
