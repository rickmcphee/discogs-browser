import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from '../App'

class MockEventSource {
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
}

const { getAuthStatus, getCrawlers, getUserHiddenCrawlers, postUserHiddenCrawlers, openLogsStream, getQueueSummary, getReleases, getPriceStatus } = vi.hoisted(() => ({
  getReleases: vi.fn(),
  getPriceStatus: vi.fn(),
  getQueueSummary: vi.fn().mockResolvedValue({
    totals: {
      claimable_rows: 0, claimable_release_rows: 0, claimable_stock_rows: 0, held_rows: 0,
      unactionable_rows: 0, in_progress_rows: 0, stranded_rows: 0, rows_done_last_hour: 0,
      eta_seconds: null, claimable_units: 0, held_units: 0, in_progress_units: 0,
    },
    crawlers: [], stranded_after_seconds: 1800, activity_window_seconds: 3600,
    pool_running: true, generated_at: '2026-08-25T00:00:00Z',
  }),
  getAuthStatus: vi.fn().mockResolvedValue({ state: 'authenticated', user: { discogs_username: 'test', is_admin: true } }),
  getCrawlers: vi.fn().mockResolvedValue([]),
  getUserHiddenCrawlers: vi.fn().mockResolvedValue([]),
  postUserHiddenCrawlers: vi.fn().mockResolvedValue(undefined),
  openLogsStream: vi.fn(),
}))

vi.mock('../api/client', () => ({
  checkHealth: vi.fn().mockResolvedValue(true),
  getAuthStatus,
  setUnauthorizedHandler: vi.fn(),
  getUserHiddenCrawlers,
  postUserHiddenCrawlers,
  refreshCollection: vi.fn().mockResolvedValue({ synced: 0, username: 'test' }),
  getCollectionStatus: vi.fn().mockResolvedValue({ total: 0, last_synced: null, sync: null }),
  getCrawlStatus: vi.fn().mockResolvedValue({ total: 0, missing: 0, oldest_checked: null }),
  postCrawlStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  getCrawlers,
  openCrawlStream: vi.fn(() => new MockEventSource()),
  getReleases,
  getArtists: vi.fn().mockResolvedValue([]),
  getSettings: vi.fn().mockResolvedValue({
    crawl_delay_seconds: 30, consecutive_failure_limit: 10, crawl_schedule: '',
    crawl_schedule_mode: 'missing',
    ebay_app_id: '', ebay_cert_id: '', stock_schedule: '',
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
  getStock: vi.fn().mockResolvedValue({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] }),
  getStockArtists: vi.fn().mockResolvedValue([]),
  postStockSyncStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  postJudgmentStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  postJudgmentStop: vi.fn().mockResolvedValue({ stopping: false, run: null }),
  getJudgmentStatus: vi.fn().mockResolvedValue({ any_judged: false, run: null }),
  getPriceStatus,
  getNotificationsUnread: vi.fn().mockResolvedValue({ unread: 0, latest_id: null }),
  markNotificationsRead: vi.fn().mockResolvedValue({ unread: 0, latest_id: null }),
  listInvites: vi.fn().mockResolvedValue([]),
  createInvite: vi.fn().mockResolvedValue({ code: '' }),
  getQueueSummary,
  getQueueNext: vi.fn().mockResolvedValue([]),
}))

beforeEach(() => {
  vi.clearAllMocks()
  openLogsStream.mockImplementation(() => new MockEventSource())
  getReleases.mockResolvedValue({ total: 0, page: 1, per_page: 50, releases: [] })
  getPriceStatus.mockResolvedValue({ any_price_paid: false })
  localStorage.clear()
})

// The tab is stored under the same prefix as App's other browser-local state.
const VIEW_KEY = 'discogs-browser.view'

const tab = (name: string) => screen.getByRole('button', { name })
const isLit = (name: string) => tab(name).className.includes('bg-white')

describe('the tab the app reopens on', () => {
  it('opens on the library tab stored from the last visit', async () => {
    localStorage.setItem(VIEW_KEY, 'store')
    render(<App />)
    await waitFor(() => expect(isLit('Store')).toBe(true))
    expect(isLit('Collection')).toBe(false)
  })

  it('stores each library tab as it is chosen', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Wantlist' }))
    await waitFor(() => expect(localStorage.getItem(VIEW_KEY)).toBe('wantlist'))
  })

  it('falls back to Collection when the stored tab is one only an admin is served', async () => {
    // Nothing mounts `logs` without admin rights (an admin viewing as a user
    // has none), so restoring it would open the app on a blank screen with no
    // tab lit -- invisible rather than merely wrong.
    getAuthStatus.mockResolvedValueOnce({ state: 'authenticated', user: { discogs_username: 'test', is_admin: false } })
    localStorage.setItem(VIEW_KEY, 'logs')
    render(<App />)
    await waitFor(() => expect(isLit('Collection')).toBe(true))
  })

  it('falls back to Collection for a stored value that is not a tab', async () => {
    localStorage.setItem(VIEW_KEY, 'tracks')
    render(<App />)
    await waitFor(() => expect(isLit('Collection')).toBe(true))
  })

  it('leaves the stored tab alone while an errand is open', async () => {
    // Account and Notifications are places you go to do something and come
    // back from, so neither is restorable -- and neither may displace the
    // library tab the user was last on.
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Store' }))
    await waitFor(() => expect(localStorage.getItem(VIEW_KEY)).toBe('store'))
    fireEvent.click(screen.getByRole('button', { name: /profile/i }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Recommendations' })).toBeInTheDocument())
    expect(localStorage.getItem(VIEW_KEY)).toBe('store')
  })

  it('comes back to that tab on the next visit, not to the errand', async () => {
    const { unmount } = render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Store' }))
    fireEvent.click(screen.getByRole('button', { name: /profile/i }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Recommendations' })).toBeInTheDocument())
    unmount()
    render(<App />)
    await waitFor(() => expect(isLit('Store')).toBe(true))
  })
})

// Both RecordBrowser instances are mounted at once, so getReleases carries
// calls for either scope and "the last call" is whichever tab refetched most
// recently. Read the Collection tab's own calls.
function lastCollectionSort(): string | undefined {
  const calls = getReleases.mock.calls
    .map(([params]) => params as { scope?: string; sort?: string })
    .filter((params) => params.scope === 'collection')
  return calls[calls.length - 1]?.sort
}

describe('a sort restored across the price-status round trip', () => {
  it('keeps a stored Price sort until the answer arrives, then holds it', async () => {
    // The Price column is hidden while the answer is outstanding, exactly as
    // it is for a user with no prices -- but the sort behind it survives.
    let answer: (v: { any_price_paid: boolean }) => void = () => {}
    getPriceStatus.mockImplementationOnce(() => new Promise((resolve) => { answer = resolve }))
    localStorage.setItem('sortField_collection', 'discogs_price')
    render(<App />)
    await waitFor(() => expect(lastCollectionSort()).toBe('discogs_price'))
    answer({ any_price_paid: true })
    await waitFor(() => expect(screen.getAllByText(/Price/).length).toBeGreaterThan(0))
    expect(lastCollectionSort()).toBe('discogs_price')
  })

  it('drops it once the answer is no, so the sort cannot outlive its column', async () => {
    getPriceStatus.mockResolvedValue({ any_price_paid: false })
    localStorage.setItem('sortField_collection', 'discogs_price')
    render(<App />)
    await waitFor(() => expect(lastCollectionSort()).toBe('artist'))
  })

  it('drops it when the price status cannot be fetched at all', async () => {
    // A failure has to answer too: left unknown, the column stays hidden while
    // the rows go on coming back in price order.
    getPriceStatus.mockRejectedValue(new Error('offline'))
    localStorage.setItem('sortField_collection', 'discogs_price')
    render(<App />)
    await waitFor(() => expect(lastCollectionSort()).toBe('artist'))
  })
})
