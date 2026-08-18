import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import App from '../App'
import { checkHealth, getAuthStatus } from '../api/client'

class MockEventSource {
  static instances: MockEventSource[] = []
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  constructor() { MockEventSource.instances.push(this) }
}

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
  getCrawlers: vi.fn().mockResolvedValue([]),
  openCrawlStream: vi.fn(() => new MockEventSource()),
  getReleases: vi.fn().mockResolvedValue({ total: 0, page: 1, per_page: 50, releases: [] }),
  getArtists: vi.fn().mockResolvedValue([]),
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
  importRecommendationsCsv: vi.fn(),
  exportRecommendationsCsv: vi.fn(),
  clearJudgments: vi.fn(),
  listInvites: vi.fn().mockResolvedValue([]),
  createInvite: vi.fn().mockResolvedValue({ code: '' }),
}))

beforeEach(() => {
  MockEventSource.instances = []
  vi.clearAllMocks()
  localStorage.clear()
  vi.mocked(checkHealth).mockResolvedValue(true)
  vi.mocked(getAuthStatus).mockResolvedValue({ state: 'authenticated', user: { discogs_username: 'test', is_admin: true } })
})

afterEach(() => {
  vi.useRealTimers()
})

// The health poll runs on a real 2000ms setTimeout, so every test here uses
// fake timers and advances the clock instead of sleeping -- deterministic
// and instant, same rationale as settings.test.tsx's debounce tests.
// vi.useFakeTimers() is called before render() in every test so no timer
// from the poll loop is ever real (a real timer created before the switch
// wouldn't be advanced by vi.advanceTimersByTimeAsync).
const advanceBy = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
// Flushes the initial mount chain (checkHealth resolves -> backendUp flips
// true -> getAuthStatus effect fires -> resolves -> authState flips
// authenticated -> app renders) without relying on waitFor, which can't
// poll once the clock is faked.
const stabilize = () => act(async () => {
  await vi.advanceTimersByTimeAsync(0)
  await vi.advanceTimersByTimeAsync(0)
})

const DOWN_MESSAGE = "Can't reach the server. Retrying…"

describe('backend-down handling', () => {
  it('shows a neutral loading state, not the down screen, before the first health check resolves', async () => {
    vi.useFakeTimers()
    vi.mocked(checkHealth).mockImplementation(() => new Promise(() => {})) // never resolves
    render(<App />)
    await advanceBy(0)

    expect(screen.getByText('Loading…')).toBeInTheDocument()
    expect(screen.queryByText(DOWN_MESSAGE)).not.toBeInTheDocument()
  })

  it('shows BackendDownScreen -- not the login screen -- only after two consecutive failed checks at load', async () => {
    vi.useFakeTimers()
    vi.mocked(checkHealth).mockResolvedValue(false)
    render(<App />)
    await advanceBy(0)

    // One failure: still no evidence of a confirmed outage, no down screen yet.
    expect(screen.queryByText(DOWN_MESSAGE)).not.toBeInTheDocument()
    expect(getAuthStatus).not.toHaveBeenCalled()

    await advanceBy(2000)
    expect(screen.getByText(DOWN_MESSAGE)).toBeInTheDocument()
    expect(getAuthStatus).not.toHaveBeenCalled()
    expect(screen.queryByText('Continue with Discogs')).not.toBeInTheDocument()
  })

  it('shows the app once the backend is reachable', async () => {
    vi.useFakeTimers()
    render(<App />)
    await stabilize()

    expect(screen.getByRole('button', { name: 'Collection' })).toBeInTheDocument()
    expect(screen.queryByText(DOWN_MESSAGE)).not.toBeInTheDocument()
  })

  it('does not flip down on a single failure between two successes', async () => {
    vi.useFakeTimers()
    render(<App />)
    await stabilize()
    expect(screen.getByRole('button', { name: 'Collection' })).toBeInTheDocument()

    vi.mocked(checkHealth).mockResolvedValueOnce(false)
    await advanceBy(2000)
    expect(screen.queryByText(DOWN_MESSAGE)).not.toBeInTheDocument()

    // Back to succeeding -- the failure counter should have reset, not
    // carried over toward the next outage.
    await advanceBy(2000)
    await advanceBy(2000)
    expect(screen.queryByText(DOWN_MESSAGE)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Collection' })).toBeInTheDocument()
  })

  it('overlays BackendDownScreen on the still-mounted app mid-session, instead of unmounting it', async () => {
    vi.useFakeTimers()
    render(<App />)
    await stabilize()
    expect(screen.getByRole('button', { name: 'Collection' })).toBeInTheDocument()

    vi.mocked(checkHealth).mockResolvedValue(false)
    await advanceBy(2000)
    expect(screen.queryByText(DOWN_MESSAGE)).not.toBeInTheDocument()

    await advanceBy(2000)
    expect(screen.getByText(DOWN_MESSAGE)).toBeInTheDocument()
    // The authenticated app is still mounted underneath the overlay --
    // its nav is still in the document, not torn down and rebuilt.
    expect(screen.getByRole('button', { name: 'Collection' })).toBeInTheDocument()
  })

  it('auto-recovers once the health check starts succeeding again, revalidating the session', async () => {
    vi.useFakeTimers()
    render(<App />)
    await stabilize()
    expect(getAuthStatus).toHaveBeenCalledTimes(1)

    vi.mocked(checkHealth).mockResolvedValue(false)
    await advanceBy(2000)
    await advanceBy(2000)
    expect(screen.getByText(DOWN_MESSAGE)).toBeInTheDocument()

    vi.mocked(checkHealth).mockResolvedValue(true)
    await advanceBy(2000)
    expect(screen.getByRole('button', { name: 'Collection' })).toBeInTheDocument()
    expect(screen.queryByText(DOWN_MESSAGE)).not.toBeInTheDocument()
    expect(getAuthStatus).toHaveBeenCalledTimes(2)
  })
})
