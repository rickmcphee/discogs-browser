import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from '../App'
import type { CollectionSyncRun, Release } from '../api/types'

// Never emits. That is the whole point: on the deployment's two Machines the
// stream and the refresh request need not land on the same one, and when they
// don't, this tab hears nothing about the sync it just asked for.
class SilentEventSource {
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
}

const { getCollectionStatus, getReleases, refreshCollection } = vi.hoisted(() => ({
  getCollectionStatus: vi.fn(),
  getReleases: vi.fn(),
  refreshCollection: vi.fn(),
}))

const release: Release = {
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
}

function run(overrides: Partial<CollectionSyncRun>): CollectionSyncRun {
  return {
    status: 'running', running: true, stale: false, mode: 'new', scope: 'all',
    page: null, total_pages: null, synced: 0, wishlist_synced: null,
    error: null, started_at: null, finished_at: null,
    ...overrides,
  }
}

vi.mock('../api/client', () => ({
  checkHealth: vi.fn().mockResolvedValue(true),
  getAuthStatus: vi.fn().mockResolvedValue({ state: 'authenticated', user: { discogs_username: 'test', is_admin: false } }),
  setUnauthorizedHandler: vi.fn(),
  getUserHiddenCrawlers: vi.fn().mockResolvedValue([]),
  postUserHiddenCrawlers: vi.fn().mockResolvedValue(undefined),
  refreshCollection,
  getCollectionStatus,
  getCrawlStatus: vi.fn().mockResolvedValue({ total: 0, missing: 0, oldest_checked: null }),
  postCrawlStart: vi.fn().mockResolvedValue({ enqueued: 0 }),
  getCrawlers: vi.fn().mockResolvedValue([]),
  openCrawlStream: vi.fn(() => new SilentEventSource()),
  getReleases,
  getArtists: vi.fn().mockResolvedValue(['Pink Floyd']),
  getSettings: vi.fn().mockResolvedValue({
    crawl_delay_seconds: 30, consecutive_failure_limit: 10, crawl_schedule: '',
    crawl_schedule_mode: 'missing', ebay_app_id: '', ebay_cert_id: '', stock_schedule: '',
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
  openLogsStream: vi.fn(() => new SilentEventSource()),
  screenshotUrl: vi.fn((path: string) => `/api/screenshots/${path}`),
  clearLogs: vi.fn(),
  getStock: vi.fn().mockResolvedValue({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] }),
  getStockArtists: vi.fn().mockResolvedValue([]),
  postStockSyncStart: vi.fn().mockResolvedValue({ started: true, on_another_instance: false, running: true }),
  postJudgmentStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  getJudgmentStatus: vi.fn().mockResolvedValue({ any_judged: false }),
  getPriceStatus: vi.fn().mockResolvedValue({ any_price_paid: false }),
  getNotificationsUnread: vi.fn().mockResolvedValue({ unread: 0, latest_id: null }),
  markNotificationsRead: vi.fn().mockResolvedValue({ unread: 0, latest_id: null }),
  clearJudgments: vi.fn(),
  exportRecommendationsCsv: vi.fn(),
  importRecommendationsCsv: vi.fn(),
  listInvites: vi.fn().mockResolvedValue([]),
  createInvite: vi.fn().mockResolvedValue({ code: '' }),
}))

// Longer than App's poll interval, so one advance lands exactly one more poll.
const PAST_ONE_POLL = 3500

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  getReleases.mockResolvedValue({ total: 1, page: 1, per_page: 250, releases: [release] })
  refreshCollection.mockResolvedValue({ started: true, running: true })
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('following a collection sync without its events', () => {
  it('reports progress and refetches the collection when the run finishes', async () => {
    getCollectionStatus
      .mockResolvedValueOnce({ total: 5, last_synced: null, sync: run({ page: 1, total_pages: 2, synced: 10 }) })
      .mockResolvedValue({
        total: 25, last_synced: null,
        sync: run({ status: 'complete', running: false, page: 2, total_pages: 2, synced: 25, wishlist_synced: 3 }),
      })

    render(<App />)
    await screen.findByText('Syncing collection… 10 records (page 1/2)')
    const whileRunning = getReleases.mock.calls.length

    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL)

    await screen.findByText('Synced 25 records, 3 wantlist items')
    // The point of the whole exercise: the table goes back for the records the
    // sync just wrote, with nothing having told it to but this poll.
    await waitFor(() => expect(getReleases.mock.calls.length).toBeGreaterThan(whileRunning))
  })

  it('reports a sync that failed on the other Machine', async () => {
    getCollectionStatus
      .mockResolvedValueOnce({ total: 5, last_synced: null, sync: run({ page: 1, total_pages: 2, synced: 10 }) })
      .mockResolvedValue({
        total: 5, last_synced: null,
        sync: run({ status: 'error', running: false, error: 'Discogs request failed' }),
      })

    render(<App />)
    await screen.findByText('Syncing collection… 10 records (page 1/2)')

    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL)

    await screen.findByText('Sync failed: Discogs request failed')
  })

  it('says so when a run stopped without finishing', async () => {
    getCollectionStatus
      .mockResolvedValueOnce({ total: 5, last_synced: null, sync: run({ page: 1, total_pages: 2, synced: 10 }) })
      .mockResolvedValue({
        total: 5, last_synced: null,
        sync: run({ running: false, stale: true, page: 1, total_pages: 2, synced: 10 }),
      })

    render(<App />)
    await screen.findByText('Syncing collection… 10 records (page 1/2)')

    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL)

    await screen.findByText('Sync stopped before it finished — sync again to pick up where it left off.')
  })

  it('does not re-announce a run that had already finished before this page loaded', async () => {
    getCollectionStatus.mockResolvedValue({
      total: 25, last_synced: null,
      sync: run({ status: 'complete', running: false, synced: 25, wishlist_synced: 3 }),
    })

    render(<App />)
    await waitFor(() => expect(getCollectionStatus).toHaveBeenCalled())
    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL)

    expect(screen.queryByText('Synced 25 records, 3 wantlist items')).toBeNull()
  })

  it('shows the running sync instead of asking which kind of refresh to start', async () => {
    // Neither of the modal's two choices could start anything while a sync
    // holds the claim, so asking is a question already answered.
    getCollectionStatus.mockResolvedValue({
      total: 5, last_synced: null, sync: run({ page: 2, total_pages: 9, synced: 150 }),
    })

    render(<App />)
    fireEvent.click(await screen.findByTitle('Sync collection from Discogs'))

    await screen.findByText('Syncing collection… 150 records (page 2/9)')
    expect(screen.queryByText('Collection already loaded')).toBeNull()
    expect(refreshCollection).not.toHaveBeenCalled()
  })

  it('says the sync could not start when the refusal was not a running sync', async () => {
    // POST /collection/refresh answers 409 for every reason start_sync
    // declines — a Plex match for this user included — so a 409 is not proof
    // that there is a sync to follow. Following one that is not there left the
    // click silent, or announced the previous run's outcome as if it were new.
    const refused: any = new Error('{"detail":"Collection sync already running"}')
    refused.status = 409
    refreshCollection.mockRejectedValue(refused)
    getCollectionStatus.mockResolvedValue({
      total: 25, last_synced: null,
      sync: run({ status: 'complete', running: false, synced: 25, wishlist_synced: 3 }),
    })

    render(<App />)
    fireEvent.click(await screen.findByTitle('Sync collection from Discogs'))
    fireEvent.click(await screen.findByRole('button', { name: /Refresh All/ }))

    await screen.findByText(/Could not start a sync/)
    // And not the completed run it found on file, which belongs to a sync this
    // click had nothing to do with.
    expect(screen.queryByText('Synced 25 records, 3 wantlist items')).toBeNull()
  })

  it('follows the running sync instead of reporting a failure when the refresh is refused', async () => {
    // 409: a sync is already running -- on this deployment, quite possibly on
    // the Machine this tab never talks to.
    const refused: any = new Error('{"detail":"Collection sync already running"}')
    refused.status = 409
    refreshCollection.mockRejectedValue(refused)
    // Two idle reads first: the mount poll, then the modal's own "is anything
    // loaded?" check. A running sync there would skip the modal entirely (see
    // the test above), which is not the path being exercised here.
    getCollectionStatus
      .mockResolvedValueOnce({ total: 5, last_synced: null, sync: null })
      .mockResolvedValueOnce({ total: 5, last_synced: null, sync: null })
      .mockResolvedValue({ total: 5, last_synced: null, sync: run({ page: 4, total_pages: 9, synced: 340 }) })

    render(<App />)
    const button = await screen.findByTitle('Sync collection from Discogs')
    fireEvent.click(button)
    fireEvent.click(await screen.findByRole('button', { name: /Refresh All/ }))

    await screen.findByText('Syncing collection… 340 records (page 4/9)')
    expect(screen.queryByText(/Sync failed/)).toBeNull()
  })
})
