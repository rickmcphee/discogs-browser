import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from '../App'

class MockEventSource {
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
}

let lastCrawlSource: MockEventSource | null = null

const { getNotifications, getNotificationsUnread, markNotificationsRead } = vi.hoisted(() => ({
  getNotifications: vi.fn(),
  getNotificationsUnread: vi.fn(),
  markNotificationsRead: vi.fn(),
}))

vi.mock('../api/client', () => ({
  checkHealth: vi.fn().mockResolvedValue(true),
  getAuthStatus: vi.fn().mockResolvedValue({
    state: 'authenticated', user: { discogs_username: 'test', is_admin: false },
  }),
  setUnauthorizedHandler: vi.fn(),
  getUserHiddenCrawlers: vi.fn().mockResolvedValue([]),
  postUserHiddenCrawlers: vi.fn().mockResolvedValue(undefined),
  refreshCollection: vi.fn().mockResolvedValue({ synced: 0, username: 'test' }),
  getCollectionStatus: vi.fn().mockResolvedValue({ total: 0, last_synced: null }),
  getCrawlStatus: vi.fn().mockResolvedValue({ total: 0, missing: 0, oldest_checked: null }),
  postCrawlStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  getCrawlers: vi.fn().mockResolvedValue([]),
  openCrawlStream: vi.fn(() => {
    lastCrawlSource = new MockEventSource()
    return lastCrawlSource
  }),
  getReleases: vi.fn().mockResolvedValue({ total: 0, page: 1, per_page: 50, releases: [] }),
  getArtists: vi.fn().mockResolvedValue([]),
  getSettings: vi.fn().mockResolvedValue({
    crawl_delay_seconds: 30, consecutive_failure_limit: 10, crawl_schedule: '',
    crawl_schedule_mode: 'missing', ebay_app_id: '', ebay_cert_id: '', stock_schedule: '',
  }),
  getUserSettings: vi.fn().mockResolvedValue({
    anthropic_api_key: '', recommendation_item_limit: 300,
    plex_base_url: '', plex_token: '', plex_match_threshold: 90,
  }),
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
  getPriceStatus: vi.fn().mockResolvedValue({ any_price_paid: false }),
  getNotifications,
  getNotificationsUnread,
  markNotificationsRead,
  listInvites: vi.fn().mockResolvedValue([]),
  createInvite: vi.fn().mockResolvedValue({ code: '' }),
  getQueueSummary: vi.fn(),
  getQueueNext: vi.fn().mockResolvedValue([]),
}))

function drop(overrides: Record<string, unknown> = {}) {
  return {
    id: 7,
    item_key: 'key-1',
    artist: 'Aphex Twin',
    title: 'Selected Ambient Works',
    format: 'LP',
    source: 'Amazon',
    url: 'https://amazon.example/x',
    price: 18.5,
    currency: 'USD',
    previous_best: 24,
    cover_image_url: null,
    created_at: '2026-08-28T12:00:00',
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  lastCrawlSource = null
  getNotificationsUnread.mockResolvedValue({ unread: 0, latest_id: null })
  getNotifications.mockResolvedValue({ items: [], unread: 0, latest_id: null, last_read_id: 0 })
  markNotificationsRead.mockResolvedValue({ unread: 0, latest_id: null })
})

describe('notification bell', () => {
  it('renders a bell in the header for every user, admin or not', async () => {
    render(<App />)
    expect(await screen.findByRole('button', { name: 'Notifications' })).toBeInTheDocument()
  })

  it('shows no dot when nothing is unread', async () => {
    render(<App />)
    await screen.findByRole('button', { name: 'Notifications' })
    await waitFor(() => expect(getNotificationsUnread).toHaveBeenCalled())
    expect(screen.queryByTestId('notification-dot')).not.toBeInTheDocument()
  })

  it('shows a dot and the unread count in its label when something is new', async () => {
    getNotificationsUnread.mockResolvedValue({ unread: 3, latest_id: 9 })
    render(<App />)
    expect(await screen.findByRole('button', { name: 'Notifications, 3 unread' })).toBeInTheDocument()
    expect(screen.getByTestId('notification-dot')).toBeInTheDocument()
  })

  it('refetches the unread count on a listing_changed SSE event', async () => {
    render(<App />)
    await waitFor(() => expect(getNotificationsUnread).toHaveBeenCalledTimes(1))

    getNotificationsUnread.mockResolvedValue({ unread: 1, latest_id: 4 })
    lastCrawlSource!.onmessage!({
      data: JSON.stringify({ id: 1, type: 'listing_changed', status: 'found', item_key: 'k', crawler_id: 3 }),
    } as MessageEvent)

    expect(await screen.findByRole('button', { name: 'Notifications, 1 unread' })).toBeInTheDocument()
  })

  it('does not load or mark notifications read until the tab is opened', async () => {
    getNotificationsUnread.mockResolvedValue({ unread: 2, latest_id: 7 })
    render(<App />)
    await screen.findByRole('button', { name: 'Notifications, 2 unread' })
    expect(getNotifications).not.toHaveBeenCalled()
    expect(markNotificationsRead).not.toHaveBeenCalled()
  })
})

describe('notifications view', () => {
  it('lists a price drop with its new price, the price it beat, its source and a link', async () => {
    getNotificationsUnread.mockResolvedValue({ unread: 1, latest_id: 7 })
    getNotifications.mockResolvedValue({ items: [drop()], unread: 1, latest_id: 7, last_read_id: 0 })

    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Notifications, 1 unread' }))

    const link = await screen.findByRole('link', { name: /Selected Ambient Works/ })
    expect(link).toHaveAttribute('href', 'https://amazon.example/x')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveTextContent('Aphex Twin')
    expect(link).toHaveTextContent('$18.50')
    expect(link).toHaveTextContent('$24.00')
    expect(link).toHaveTextContent('at Amazon')
  })

  it('marks everything read on open, clearing the dot', async () => {
    getNotificationsUnread.mockResolvedValue({ unread: 1, latest_id: 7 })
    getNotifications.mockResolvedValue({ items: [drop()], unread: 1, latest_id: 7, last_read_id: 0 })

    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Notifications, 1 unread' }))

    await waitFor(() => expect(markNotificationsRead).toHaveBeenCalledWith(7))
    await waitFor(() => expect(screen.queryByTestId('notification-dot')).not.toBeInTheDocument())
  })

  it('leaves the dot lit when the read request fails', async () => {
    // Clearing optimistically made a dropped POST look like success. The price
    // change that produced the unread rows has already happened, so nothing
    // guarantees a later generation tick to correct the badge -- it would stay
    // wrong until an unrelated change or a reload.
    getNotificationsUnread.mockResolvedValue({ unread: 1, latest_id: 7 })
    getNotifications.mockResolvedValue({ items: [drop()], unread: 1, latest_id: 7, last_read_id: 0 })
    markNotificationsRead.mockRejectedValue(new Error('offline'))

    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Notifications, 1 unread' }))

    await waitFor(() => expect(markNotificationsRead).toHaveBeenCalledWith(7))
    await screen.findByRole('link', { name: /Selected Ambient Works/ })
    expect(screen.getByTestId('notification-dot')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Notifications, 1 unread' })).toBeInTheDocument()
  })

  it('keeps the unread accent on rows that were new when the tab was opened', async () => {
    getNotificationsUnread.mockResolvedValue({ unread: 1, latest_id: 7 })
    getNotifications.mockResolvedValue({
      items: [drop({ id: 7 }), drop({ id: 6, title: 'Older Album', item_key: 'key-2' })],
      unread: 1, latest_id: 7, last_read_id: 6,
    })

    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Notifications, 1 unread' }))

    const fresh = await screen.findByRole('link', { name: /Selected Ambient Works/ })
    const older = screen.getByRole('link', { name: /Older Album/ })
    expect(fresh).toHaveAttribute('data-unread', 'true')
    expect(older).toHaveAttribute('data-unread', 'false')
  })

  it('explains itself when the user has no notifications yet', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Notifications' }))
    expect(await screen.findByText(/Save an item on the Store tab/)).toBeInTheDocument()
    // Nothing to mark read, so nothing is written.
    expect(markNotificationsRead).not.toHaveBeenCalled()
  })

  it('reloads while open when an SSE tick says prices moved', async () => {
    getNotifications.mockResolvedValue({ items: [], unread: 0, latest_id: null, last_read_id: 0 })
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Notifications' }))
    await waitFor(() => expect(getNotifications).toHaveBeenCalledTimes(1))

    getNotifications.mockResolvedValue({ items: [drop()], unread: 1, latest_id: 7, last_read_id: 0 })
    lastCrawlSource!.onmessage!({
      data: JSON.stringify({ id: 2, type: 'listing_changed', status: 'found', item_key: 'k', crawler_id: 3 }),
    } as MessageEvent)

    expect(await screen.findByRole('link', { name: /Selected Ambient Works/ })).toBeInTheDocument()
  })
})
