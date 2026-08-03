import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from '../App'

class MockEventSource {
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
}

const { getAuthStatus, getCrawlers } = vi.hoisted(() => ({
  getAuthStatus: vi.fn().mockResolvedValue({ state: 'authenticated', user: { discogs_username: 'test', is_admin: true } }),
  getCrawlers: vi.fn().mockResolvedValue([]),
}))

vi.mock('../api/client', () => ({
  checkHealth: vi.fn().mockResolvedValue(true),
  getAuthStatus,
  setUnauthorizedHandler: vi.fn(),
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
  openLogsStream: vi.fn(() => new MockEventSource()),
  screenshotUrl: vi.fn((path: string) => `/api/screenshots/${path}`),
  clearLogs: vi.fn(),
  getStock: vi.fn().mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] }),
  getStockArtists: vi.fn().mockResolvedValue([]),
  postStockSyncStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  postJudgmentStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  getJudgmentStatus: vi.fn().mockResolvedValue({ any_judged: false }),
}))

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
})

describe('header profile navigation', () => {
  it('switches to the Account view when the avatar button is clicked', async () => {
    render(<App />)
    const button = await screen.findByRole('button', { name: /profile/i })
    fireEvent.click(button)
    await waitFor(() => expect(button.className).toContain('ring-2 ring-indigo-500'))
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

  it('hides the Logs nav button but shows Settings for a non-admin user', async () => {
    getAuthStatus.mockResolvedValueOnce({ state: 'authenticated', user: { discogs_username: 'test', is_admin: false } })
    render(<App />)
    await screen.findByRole('button', { name: 'Store' })
    expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Logs' })).not.toBeInTheDocument()
  })

  it('shows the role switch to an admin, and toggling it hides Logs (but not Settings) until toggled back', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /profile/i }))
    const roleSwitch = await screen.findByRole('switch', { name: 'Toggle admin/user view' })
    expect(roleSwitch).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Logs' })).toBeInTheDocument()

    fireEvent.click(roleSwitch)
    expect(roleSwitch).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument()
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
    expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Logs' })).not.toBeInTheDocument()
  })

  it('does not show the role switch to a non-admin', async () => {
    getAuthStatus.mockResolvedValueOnce({ state: 'authenticated', user: { discogs_username: 'test', is_admin: false } })
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /profile/i }))
    await screen.findByRole('heading', { name: 'Recommendations' })
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  })
})

describe('hiddenCrawlerIds persistence', () => {
  const HIDDEN_CRAWLER_IDS_KEY = 'discogs-browser.hiddenCrawlerIds'
  const crawler = {
    id: 7, site_name: 'TestStore', module_path: '', crawler_type: 'release' as const,
    enabled: true, last_run: null, base_url: null,
  }

  beforeEach(() => {
    localStorage.removeItem(HIDDEN_CRAWLER_IDS_KEY)
  })

  it('toggling a crawler View button writes localStorage, and the hidden state survives a remount', async () => {
    getCrawlers.mockResolvedValueOnce([crawler])
    const { unmount } = render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Settings' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Visible' }))

    await waitFor(() => expect(screen.getByRole('button', { name: 'Hidden' })).toBeInTheDocument())
    expect(JSON.parse(localStorage.getItem(HIDDEN_CRAWLER_IDS_KEY) ?? '[]')).toEqual([7])
    unmount()

    // A browser refresh remounts the app; the persisted hidden id must be re-read on init.
    getCrawlers.mockResolvedValueOnce([crawler])
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Settings' }))
    expect(await screen.findByRole('button', { name: 'Hidden' })).toBeInTheDocument()
  })
})
