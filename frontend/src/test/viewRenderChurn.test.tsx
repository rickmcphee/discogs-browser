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

const { release, stockSpy, settingsSpy, accountSpy, logViewerSpy } = vi.hoisted(() => ({
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
  stockSpy: vi.fn(() => null),
  settingsSpy: vi.fn(() => null),
  accountSpy: vi.fn(() => null),
  logViewerSpy: vi.fn(() => null),
}))

// These stand-ins are wrapped in memo the same way the real views are
// expected to be. That isolates what this test checks — does App.tsx pass
// these views referentially/value-stable props? — from whether the real
// view files remember to apply memo, which viewMemoization.test.ts checks
// directly against the unmocked modules.
vi.mock('../views/StockBrowser', async () => {
  const { memo } = await import('react')
  return { default: memo(stockSpy) }
})
vi.mock('../views/Settings', async () => {
  const { memo } = await import('react')
  return { default: memo(settingsSpy) }
})
vi.mock('../views/Account', async () => {
  const { memo } = await import('react')
  return { default: memo(accountSpy) }
})
vi.mock('../views/LogViewer', async () => {
  const { memo } = await import('react')
  return { default: memo(logViewerSpy) }
})

vi.mock('../api/client', () => ({
  checkHealth: vi.fn().mockResolvedValue(true),
  getAuthState: vi.fn().mockResolvedValue('authenticated'),
  setUnauthorizedHandler: vi.fn(),
  refreshCollection: vi.fn().mockResolvedValue({ synced: 0, username: 'test' }),
  getCollectionStatus: vi.fn().mockResolvedValue({ total: 0, last_synced: null }),
  getCrawlStatus: vi.fn().mockResolvedValue({ total: 0, missing: 0, oldest_checked: null }),
  postCrawlStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  getCrawlers: vi.fn().mockResolvedValue([
    { id: 1, site_name: 'Amazon', module_path: 'amazon', crawler_type: 'release', enabled: true },
  ]),
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

describe('views unrelated to crawl progress do not re-render on every crawl event', () => {
  it('does not re-invoke StockBrowser, Settings, Account, or LogViewer while prices are refreshing', async () => {
    render(<App />)
    // Wait for the post-login poll's one-time crawler fetch to settle (it swaps
    // in a fresh `crawlers` array, which legitimately re-renders Settings once)
    // before snapshotting, so that unrelated startup settling isn't mistaken
    // for churn caused by the crawl event stream this test actually targets.
    // Both the Collection and Wishlist RecordBrowser instances render this
    // column header simultaneously, since App keeps every view mounted.
    await screen.findAllByText('Amazon')
    await waitFor(() => expect(stockSpy).toHaveBeenCalled())

    const callsBefore = {
      stock: stockSpy.mock.calls.length,
      settings: settingsSpy.mock.calls.length,
      account: accountSpy.mock.calls.length,
      logs: logViewerSpy.mock.calls.length,
    }

    const src = await clickRefreshAndGetSource()
    src.emit({ status: 'started', total: 2, id: 1 })
    src.emit({ status: 'found', discogs_id: 'r1', release: 'The Wall', artist: 'Pink Floyd', site: 'Amazon', price: 24.99 })
    src.emit({ status: 'found', discogs_id: 'r1', release: 'The Wall', artist: 'Pink Floyd', site: 'eBay', price: 19.99 })
    await waitFor(() => expect(screen.getByText('eBay')).toBeInTheDocument())

    expect(stockSpy.mock.calls.length).toBe(callsBefore.stock)
    expect(settingsSpy.mock.calls.length).toBe(callsBefore.settings)
    expect(accountSpy.mock.calls.length).toBe(callsBefore.account)
    expect(logViewerSpy.mock.calls.length).toBe(callsBefore.logs)
  })
})
