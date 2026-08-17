import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
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
    plex_matched_at: null,
    last_synced: '',
    date_added: null,
  } as Release,
  stockSpy: vi.fn((_props: any) => null),
  settingsSpy: vi.fn((_props: any) => null),
  accountSpy: vi.fn((_props: any) => null),
  logViewerSpy: vi.fn((_props: any) => null),
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
  getAuthStatus: vi.fn().mockResolvedValue({ state: 'authenticated', user: { discogs_username: 'test', is_admin: true } }),
  setUnauthorizedHandler: vi.fn(),
  getUserHiddenCrawlers: vi.fn().mockResolvedValue([]),
  postUserHiddenCrawlers: vi.fn().mockResolvedValue(undefined),
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
    crawl_delay_seconds: 30, consecutive_failure_limit: 10, crawl_schedule: '',
    crawl_schedule_mode: 'missing',
    ebay_app_id: '', ebay_cert_id: '', stock_schedule: '',
  }),
  getUserSettings: vi.fn().mockResolvedValue({ anthropic_api_key: '', recommendation_item_limit: 300, plex_base_url: '', plex_token: '', plex_match_threshold: 90 }),
  saveSettings: vi.fn(),
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

async function getCrawlSourceOnMount() {
  await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
  return getLastCrawlSource()
}

describe('views unrelated to crawl progress do not re-render on every crawl event', () => {
  it('does not re-invoke StockBrowser, Settings, Account, or LogViewer while prices are refreshing', async () => {
    render(<App />)
    // Wait for the post-login poll's one-time crawler fetch to settle (it swaps
    // in a fresh `crawlers` array, which legitimately re-renders Settings once)
    // before snapshotting, so that unrelated startup settling isn't mistaken
    // for churn caused by the crawl event stream this test actually targets.
    // Waiting for the first Settings render isn't enough — that one happens
    // with the empty initial array, and the swap can then land inside the
    // measured window.
    await waitFor(() =>
      expect(settingsSpy.mock.calls.some(([props]) => props.crawlers.length > 0)).toBe(true)
    )
    await waitFor(() => expect(stockSpy).toHaveBeenCalled())

    const callsBefore = {
      stock: stockSpy.mock.calls.length,
      settings: settingsSpy.mock.calls.length,
      account: accountSpy.mock.calls.length,
      logs: logViewerSpy.mock.calls.length,
    }

    const src = await getCrawlSourceOnMount()
    src.emit({ status: 'started', total: 2, id: 1 })
    src.emit({ status: 'found', discogs_id: 'r1', release: 'The Wall', artist: 'Pink Floyd', site: 'Amazon', price: 24.99 })
    src.emit({ status: 'found', discogs_id: 'r1', release: 'The Wall', artist: 'Pink Floyd', site: 'eBay', price: 19.99 })
    await waitFor(() => expect(screen.getByText(/2\/2/)).toBeInTheDocument())

    expect(stockSpy.mock.calls.length).toBe(callsBefore.stock)
    expect(settingsSpy.mock.calls.length).toBe(callsBefore.settings)
    expect(accountSpy.mock.calls.length).toBe(callsBefore.account)
    expect(logViewerSpy.mock.calls.length).toBe(callsBefore.logs)
  })
})
