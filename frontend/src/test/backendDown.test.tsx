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
  discogsLoginUrl: vi.fn(() => '/api/auth/discogs/start'),
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
  getPriceStatus: vi.fn().mockResolvedValue({ any_price_paid: false }),
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
    const collectionButton = screen.getByRole('button', { name: 'Collection' })
    expect(collectionButton).toBeInTheDocument()
    // Not inert while the backend is up -- the app is fully interactive.
    expect(collectionButton.closest('[inert]')).toBeNull()

    vi.mocked(checkHealth).mockResolvedValue(false)
    await advanceBy(2000)
    expect(screen.queryByText(DOWN_MESSAGE)).not.toBeInTheDocument()

    await advanceBy(2000)
    expect(screen.getByText(DOWN_MESSAGE)).toBeInTheDocument()
    // The authenticated app is still mounted underneath the overlay --
    // its nav is still in the document, not torn down and rebuilt --
    // but marked inert so a keyboard/screen-reader user can't reach it.
    expect(collectionButton).toBeInTheDocument()
    expect(collectionButton.closest('[inert]')).not.toBeNull()
    // The overlay itself must NOT be inert, or it'd be unreachable too.
    // Found by its message rather than by role: the status bar's live region
    // is a second role="status", and it is deliberately inside the inert
    // subtree, so a bare role query would be ambiguous.
    expect(screen.getByText(DOWN_MESSAGE).closest('[inert]')).toBeNull()
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

  it('discards a stale getAuthStatus response superseded by a later recovery', async () => {
    vi.useFakeTimers()
    render(<App />)
    await stabilize()
    expect(getAuthStatus).toHaveBeenCalledTimes(1)

    // First outage + recovery: its getAuthStatus call is deliberately left
    // pending, simulating a slow response that outlives a second flap.
    let resolveFirstRecovery: (v: unknown) => void = () => {}
    const firstRecoveryCall = new Promise((resolve) => { resolveFirstRecovery = resolve })
    vi.mocked(getAuthStatus).mockReturnValueOnce(firstRecoveryCall as ReturnType<typeof getAuthStatus>)

    vi.mocked(checkHealth).mockResolvedValue(false)
    await advanceBy(2000)
    await advanceBy(2000) // backendUp -> false (2 consecutive failures)
    vi.mocked(checkHealth).mockResolvedValue(true)
    await advanceBy(2000) // backendUp -> true, fires the (still-pending) 2nd getAuthStatus call
    expect(getAuthStatus).toHaveBeenCalledTimes(2)

    // Second outage + recovery, resolving before the first call ever does.
    vi.mocked(checkHealth).mockResolvedValue(false)
    await advanceBy(2000)
    await advanceBy(2000) // backendUp -> false again
    vi.mocked(getAuthStatus).mockResolvedValueOnce({ state: 'unauthenticated' })
    vi.mocked(checkHealth).mockResolvedValue(true)
    await advanceBy(2000) // backendUp -> true again, fires the 3rd (fast) call
    expect(getAuthStatus).toHaveBeenCalledTimes(3)
    expect(screen.getByText('Continue with Discogs')).toBeInTheDocument()

    // The first (now-stale) call finally resolves -- it must not clobber
    // the newer 'unauthenticated' result with this older 'authenticated' one.
    resolveFirstRecovery({ state: 'authenticated', user: { discogs_username: 'test', is_admin: true } })
    await advanceBy(0)
    expect(screen.getByText('Continue with Discogs')).toBeInTheDocument()
  })

  it('keeps the overlay and inert state active until session revalidation actually resolves, not just once backendUp flips true', async () => {
    vi.useFakeTimers()
    render(<App />)
    await stabilize()
    const collectionButton = screen.getByRole('button', { name: 'Collection' })

    vi.mocked(checkHealth).mockResolvedValue(false)
    await advanceBy(2000)
    await advanceBy(2000) // backendUp -> false
    expect(screen.getByText(DOWN_MESSAGE)).toBeInTheDocument()

    // Recovery: checkHealth succeeds (backendUp -> true), but the
    // revalidation fetch it kicks off is left deliberately pending.
    let resolveRevalidation: (v: unknown) => void = () => {}
    const revalidation = new Promise((resolve) => { resolveRevalidation = resolve })
    vi.mocked(getAuthStatus).mockReturnValueOnce(revalidation as ReturnType<typeof getAuthStatus>)
    vi.mocked(checkHealth).mockResolvedValue(true)
    await advanceBy(2000)

    // backendUp is true now, but the overlay must still be up -- the app
    // hasn't been reconfirmed reachable-with-a-valid-session yet.
    expect(screen.getByText(DOWN_MESSAGE)).toBeInTheDocument()
    expect(collectionButton.closest('[inert]')).not.toBeNull()

    resolveRevalidation({ state: 'authenticated', user: { discogs_username: 'test', is_admin: true } })
    await advanceBy(0)
    expect(screen.queryByText(DOWN_MESSAGE)).not.toBeInTheDocument()
    expect(collectionButton.closest('[inert]')).toBeNull()
  })
})
