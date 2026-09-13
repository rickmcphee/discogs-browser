import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from '../App'
import type { CollectionSyncRun, Release } from '../api/types'

// Never emits. That is the whole point: on the deployment's two Machines the
// stream and the refresh request need not land on the same one, and when they
// don't, this tab hears nothing about the sync it just asked for.
class SilentEventSource {
  static instances: SilentEventSource[] = []
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  constructor() { SilentEventSource.instances.push(this) }
  // Only ever called by a test that wants to put something *else* on the
  // shared banner; the collection sync's own events never arrive here.
  emit(data: object) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent)
  }
}

const { getCollectionStatus, getReleases, refreshCollection, checkHealth, getAuthStatus } = vi.hoisted(() => ({
  getCollectionStatus: vi.fn(),
  getReleases: vi.fn(),
  refreshCollection: vi.fn(),
  checkHealth: vi.fn(),
  // A fresh object per call, as the real one returns: App revalidates auth on
  // every backend down/up transition, and it is that new identity -- not any
  // change of state -- that restarts effects keyed on authState.
  getAuthStatus: vi.fn(async () => ({
    state: 'authenticated', user: { discogs_username: 'test', is_admin: false },
  })),
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
  checkHealth,
  getAuthStatus,
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
  SilentEventSource.instances = []
  vi.clearAllMocks()
  localStorage.clear()
  checkHealth.mockResolvedValue(true)
  getCollectionStatus.mockReset()
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

  it('keeps following a sync across a backend blip', async () => {
    // The app replaces authState after every backend down/up transition. The
    // follow has to survive that: a sync that finishes during the outage is
    // still this click's answer, and a restarted poll that took it for an old
    // run would leave the collection stale -- the very failure this change
    // exists to remove, reached by a different road.
    let blipped = false
    let finished = false
    getCollectionStatus.mockImplementation(async () => {
      if (blipped && !finished) throw new Error('network')
      return finished
        ? {
            total: 25, last_synced: null,
            sync: run({ status: 'complete', running: false, synced: 25, wishlist_synced: 3 }),
          }
        : { total: 5, last_synced: null, sync: run({ page: 1, total_pages: 2, synced: 10 }) }
    })

    render(<App />)
    await screen.findByText('Syncing collection… 10 records (page 1/2)')
    const whileRunning = getReleases.mock.calls.length

    // Backend goes away: two failed health polls flip the app to its
    // down screen, and the recovery replaces authState.
    blipped = true
    checkHealth.mockResolvedValue(false)
    await vi.advanceTimersByTimeAsync(6000)
    checkHealth.mockResolvedValue(true)
    await vi.advanceTimersByTimeAsync(4000)

    // The sync finished while nobody could see it.
    finished = true
    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL * 2)

    await screen.findByText('Synced 25 records, 3 wantlist items')
    await waitFor(() => expect(getReleases.mock.calls.length).toBeGreaterThan(whileRunning))
  })

  it('reports the sync outcome when it is the Plex phase that went quiet', async () => {
    // `stale` covers both phases that hold the claim, but only one of them is
    // the sync. A Plex match that dies after the sync committed its rows and
    // its counts must not send the user back to redo work that is done.
    getCollectionStatus
      .mockResolvedValueOnce({
        total: 5, last_synced: null, sync: run({ page: 1, total_pages: 2, synced: 10 }),
      })
      .mockResolvedValue({
        total: 25, last_synced: null,
        sync: run({
          status: 'plex_matching', running: false, stale: true,
          synced: 25, wishlist_synced: 3,
        }),
      })

    render(<App />)
    await screen.findByText('Syncing collection… 10 records (page 1/2)')

    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL)

    await screen.findByText('Synced 25 records, 3 wantlist items')
    expect(screen.queryByText(/Sync stopped before it finished/)).toBeNull()
  })

  it('leaves the outcome to the stream when the stream delivered it', async () => {
    // Same Machine: the SSE handlers report the sync themselves, and the Plex
    // phase that follows speaks next. A poll that still considered the run
    // its own would republish the sync's outcome over that newer line.
    getCollectionStatus
      .mockResolvedValueOnce({
        total: 5, last_synced: null, sync: run({ page: 1, total_pages: 2, synced: 10 }),
      })
      .mockResolvedValue({
        total: 25, last_synced: null,
        sync: run({
          status: 'plex_matching', running: false, synced: 25, wishlist_synced: 3,
        }),
      })

    render(<App />)
    await screen.findByText('Syncing collection… 10 records (page 1/2)')

    const stream = SilentEventSource.instances[0]
    stream.emit({
      status: 'sync_complete', synced: 25, wishlist_synced: 3, username: 'alice', id: 1,
    })
    await screen.findByText('Synced 25 records for alice, 3 wantlist items')
    stream.emit({ status: 'plex_match_started', id: 2 })
    await screen.findByText('Matching collection against Plex…')

    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL * 2)

    expect(screen.getByText('Matching collection against Plex…')).toBeTruthy()
  })

  it('still refetches when a replayed terminal event arrives for an older sync', async () => {
    // routers/crawl.py replays the whole retained buffer on reconnect, so a
    // sync_complete from an earlier sync can land while this one is running on
    // the other Machine. Treating it as this run's ending drops the follow,
    // and if this run then finishes before the next tick can re-adopt it, its
    // terminal row reads as an old one and the collection is never refetched
    // -- the original failure, by a new road. The banner line may be skipped
    // in that window, since the stream has just spoken one; the refetch may
    // not be.
    getCollectionStatus
      .mockResolvedValueOnce({
        total: 5, last_synced: null, sync: run({ page: 1, total_pages: 2, synced: 10 }),
      })
      .mockResolvedValue({
        total: 25, last_synced: null,
        sync: run({ status: 'complete', running: false, synced: 25, wishlist_synced: 3 }),
      })

    render(<App />)
    await screen.findByText('Syncing collection… 10 records (page 1/2)')

    // A stale completion, replayed on reconnect, for a sync that ended earlier.
    // It refetches on its own account, so let that settle before counting.
    SilentEventSource.instances[0].emit({
      status: 'sync_complete', synced: 7, wishlist_synced: 0, username: 'alice', id: 1,
    })
    await screen.findByText('Synced 7 records for alice, 0 wantlist items')
    await waitFor(() => expect(getReleases.mock.calls.length).toBeGreaterThan(1))
    const afterStaleEvent = getReleases.mock.calls.length

    // The run this tab was actually following ends on the very next tick,
    // with no chance to re-adopt in between.
    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL)

    await waitFor(() => expect(getReleases.mock.calls.length).toBeGreaterThan(afterStaleEvent))
  })

  it('does not talk over another job while the run sits unchanged', async () => {
    // The banner is shared with the stock sync, the judgment run and the price
    // refresh, any of which can run alongside a collection sync. Repeating an
    // unchanged line every three seconds would overwrite whatever they said.
    getCollectionStatus.mockResolvedValue({
      total: 5, last_synced: null, sync: run({ page: 1, total_pages: 2, synced: 10 }),
    })

    render(<App />)
    await screen.findByText('Syncing collection… 10 records (page 1/2)')

    SilentEventSource.instances[0].emit({
      status: 'stock_sync_progress', synced: 5, source: 'Amoeba', id: 1,
    })
    await screen.findByText('Syncing in-stock catalog… 5 items (Amoeba)')

    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL * 2)

    expect(screen.getByText('Syncing in-stock catalog… 5 items (Amoeba)')).toBeTruthy()
    expect(screen.queryByText('Syncing collection… 10 records (page 1/2)')).toBeNull()
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

  it('retries the mount read that discovers a sync already under way', async () => {
    // The effect no longer restarts on a backend revalidation, so giving up on
    // the first failed discovery read is giving up for good: a tab loaded
    // while the other Machine is mid-sync would never find it.
    getCollectionStatus
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValue({
        total: 5, last_synced: null, sync: run({ page: 3, total_pages: 9, synced: 210 }),
      })

    render(<App />)
    // Long enough to cover the first read landing, its retry, and the sleep
    // between them, wherever in the mount sequence the loop actually starts.
    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL * 3)

    await screen.findByText('Syncing collection… 210 records (page 3/9)')
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

  it('still reports a refused start when the first status reads fail', async () => {
    // The refusal has said nothing yet at this point: whether it was a running
    // sync or another job is exactly what the poll is there to find out. Going
    // quiet on a transient read failure puts the click back to looking like a
    // no-op.
    const refused: any = new Error('{"detail":"Collection sync already running"}')
    refused.status = 409
    refreshCollection.mockRejectedValue(refused)
    getCollectionStatus
      .mockResolvedValueOnce({ total: 5, last_synced: null, sync: null })
      .mockResolvedValueOnce({ total: 5, last_synced: null, sync: null })
      .mockRejectedValue(new Error('network'))

    render(<App />)
    fireEvent.click(await screen.findByTitle('Sync collection from Discogs'))
    fireEvent.click(await screen.findByRole('button', { name: /Refresh All/ }))

    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL * 4)
    await screen.findByText(/Could not start a sync/)
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

  it('still refetches when a refused start finds only a finished run', async () => {
    // A sync on the other Machine can finish between the 409 and this poll's
    // first read. The row it leaves behind is indistinguishable from one that
    // ended last week — the refusal never named the run that caused it — so
    // the banner cannot tell which, and says the click could not start a sync.
    // The table must not also be left showing the library from before a sync
    // that has just finished: that is the reported bug, reached down a
    // different road. So the refetch happens regardless of what the banner
    // decides.
    const refused: any = new Error('{"detail":"Collection sync already running"}')
    refused.status = 409
    refreshCollection.mockRejectedValue(refused)
    getCollectionStatus.mockResolvedValue({
      total: 25, last_synced: null,
      sync: run({ status: 'complete', running: false, synced: 25, wishlist_synced: 3 }),
    })

    render(<App />)
    await screen.findAllByText('The Wall')
    // Let mount settle: the initial loads must not be mistaken for the refetch
    // this test is about. Stable across a full poll interval, then measured.
    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL)
    const settled = getReleases.mock.calls.length
    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL)
    expect(getReleases.mock.calls.length).toBe(settled)

    fireEvent.click(screen.getByTitle('Sync collection from Discogs'))
    fireEvent.click(await screen.findByRole('button', { name: /Refresh All/ }))
    await screen.findByText(/Could not start a sync/)

    await waitFor(() => expect(getReleases.mock.calls.length).toBeGreaterThan(settled))
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
