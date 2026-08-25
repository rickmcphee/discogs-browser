import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from '../App'

class MockEventSource {
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
}

const { getAuthStatus, getCrawlers, getUserHiddenCrawlers, postUserHiddenCrawlers, openLogsStream, getQueueSummary } = vi.hoisted(() => ({
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
  getCollectionStatus: vi.fn().mockResolvedValue({ total: 0, last_synced: null }),
  getCrawlStatus: vi.fn().mockResolvedValue({ total: 0, missing: 0, oldest_checked: null }),
  postCrawlStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  getCrawlers,
  openCrawlStream: vi.fn(() => new MockEventSource()),
  getReleases: vi.fn().mockResolvedValue({ total: 0, page: 1, per_page: 50, releases: [] }),
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
  getStock: vi.fn().mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] }),
  getStockArtists: vi.fn().mockResolvedValue([]),
  postStockSyncStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  postJudgmentStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  getJudgmentStatus: vi.fn().mockResolvedValue({ any_judged: false }),
  getPriceStatus: vi.fn().mockResolvedValue({ any_price_paid: false }),
  listInvites: vi.fn().mockResolvedValue([]),
  createInvite: vi.fn().mockResolvedValue({ code: '' }),
  getQueueSummary,
  getQueueNext: vi.fn().mockResolvedValue([]),
}))

beforeEach(() => {
  vi.clearAllMocks()
  openLogsStream.mockImplementation(() => new MockEventSource())
  localStorage.clear()
})

describe('header profile navigation', () => {
  it('switches to the Account view when the avatar button is clicked', async () => {
    render(<App />)
    const button = await screen.findByRole('button', { name: /profile/i })
    fireEvent.click(button)
    await waitFor(() => expect(button.className).toContain('ring-2 ring-white'))
    expect(screen.getByRole('heading', { name: 'Recommendations' })).toBeInTheDocument()
  })

  it('places the profile avatar as the rightmost header control', async () => {
    render(<App />)
    const profile = await screen.findByRole('button', { name: /profile/i })
    const logs = screen.getByRole('button', { name: 'Logs' })
    // Profile must come after Logs in DOM order (rightmost in the header)
    expect(
      logs.compareDocumentPosition(profile) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
  })

  it('hides the Logs and Settings nav buttons for a non-admin user', async () => {
    getAuthStatus.mockResolvedValueOnce({ state: 'authenticated', user: { discogs_username: 'test', is_admin: false } })
    render(<App />)
    await screen.findByRole('button', { name: 'Store' })
    expect(screen.queryByRole('button', { name: 'Settings' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Logs' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Queue' })).not.toBeInTheDocument()
  })

  it('does not mount the queue view -- or poll the queue -- for a non-admin', async () => {
    // Same reasoning as the log viewer below: QueueView polls the shared crawl
    // queue from mount, so a hidden-but-mounted copy would have every invited
    // user querying admin-only state on a timer.
    getAuthStatus.mockResolvedValueOnce({ state: 'authenticated', user: { discogs_username: 'test', is_admin: false } })
    render(<App />)
    await screen.findByRole('button', { name: 'Store' })
    expect(getQueueSummary).not.toHaveBeenCalled()
  })

  it('mounts the queue view and loads the summary when an admin opens the Queue tab', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Queue' }))
    await waitFor(() => expect(getQueueSummary).toHaveBeenCalled())
    expect(await screen.findByText('Worker pool')).toBeInTheDocument()
  })

  it('does not poll the queue until the Queue tab is opened', async () => {
    render(<App />)
    await screen.findByRole('button', { name: 'Queue' })
    expect(getQueueSummary).not.toHaveBeenCalled()
  })

  it('shows the role switch to an admin, and toggling it hides Logs and Settings until toggled back', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /profile/i }))
    const roleSwitch = await screen.findByRole('switch', { name: 'Toggle admin/user view' })
    expect(roleSwitch).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Logs' })).toBeInTheDocument()

    fireEvent.click(roleSwitch)
    expect(roleSwitch).toHaveAttribute('aria-checked', 'true')
    expect(screen.queryByRole('button', { name: 'Settings' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Logs' })).not.toBeInTheDocument()

    fireEvent.click(roleSwitch)
    expect(roleSwitch).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument()
  })

  it('persists the view-as-user toggle to localStorage and restores it on remount', async () => {
    const { unmount } = render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /profile/i }))
    const roleSwitch = await screen.findByRole('switch', { name: 'Toggle admin/user view' })
    fireEvent.click(roleSwitch)
    expect(localStorage.getItem('discogs-browser.viewAsUser')).toBe('true')
    unmount()

    render(<App />)
    await screen.findByRole('button', { name: /profile/i })
    expect(screen.queryByRole('button', { name: 'Settings' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Logs' })).not.toBeInTheDocument()
  })

  it('does not show the role switch to a non-admin', async () => {
    getAuthStatus.mockResolvedValueOnce({ state: 'authenticated', user: { discogs_username: 'test', is_admin: false } })
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /profile/i }))
    await screen.findByRole('heading', { name: 'Recommendations' })
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  })

  it('does not mount the log viewer -- or open its stream -- for a non-admin', async () => {
    // The hidden-div mount pattern the other views use would still run
    // LogViewer's mount effect, handing every invited user an open SSE stream
    // of the operator's application log.
    getAuthStatus.mockResolvedValueOnce({ state: 'authenticated', user: { discogs_username: 'test', is_admin: false } })
    render(<App />)
    await screen.findByRole('button', { name: 'Store' })
    expect(openLogsStream).not.toHaveBeenCalled()
  })

  it('mounts the log viewer and opens its stream for an admin', async () => {
    render(<App />)
    await screen.findByRole('button', { name: 'Logs' })
    await waitFor(() => expect(openLogsStream).toHaveBeenCalled())
  })

  it('does not show the Settings nav button to a non-admin', async () => {
    getAuthStatus.mockResolvedValueOnce({ state: 'authenticated', user: { discogs_username: 'test', is_admin: false } })
    render(<App />)
    await screen.findByRole('button', { name: /profile/i })
    expect(screen.queryByRole('button', { name: 'Settings' })).not.toBeInTheDocument()
  })
})

describe('source filter persistence', () => {
  const crawler = {
    id: 7, site_name: 'TestStore', module_path: '', crawler_type: 'release' as const,
    enabled: true, last_run: null, base_url: null, genre: 'marketplace' as const,
  }

  it('fetches the hidden set on mount and toggling a store in the Source dropdown posts the updated set', async () => {
    getCrawlers.mockResolvedValueOnce([crawler])
    render(<App />)
    await waitFor(() => expect(getUserHiddenCrawlers).toHaveBeenCalled())

    fireEvent.click(await screen.findByText('Store'))
    const sourceButtons = await screen.findAllByRole('button', { name: 'Source' })
    fireEvent.click(sourceButtons[0])
    fireEvent.click(await screen.findByRole('checkbox', { name: 'TestStore' }))

    await waitFor(() => expect(postUserHiddenCrawlers).toHaveBeenCalledWith([7]))
  })

  it('reflects a server-persisted hidden crawler as unchecked after mount', async () => {
    getCrawlers.mockResolvedValueOnce([crawler])
    getUserHiddenCrawlers.mockResolvedValueOnce([7])
    render(<App />)

    fireEvent.click(await screen.findByText('Store'))
    const sourceButtons = await screen.findAllByRole('button', { name: 'Source' })
    fireEvent.click(sourceButtons[0])
    expect(await screen.findByRole('checkbox', { name: 'TestStore' })).not.toBeChecked()
  })
})
