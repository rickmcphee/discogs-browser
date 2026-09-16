import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import App from '../App'
import type { StockJudgmentRun } from '../api/types'

// Never emits. That is the point: the deployment's two Machines fan SSE out
// in-process, so a browser whose stream landed on the other one hears no
// stock_judgment_* event for the run it just started. The Refresh/Stop button
// has to work anyway, which means it works off GET /stock/judge/status.
class SilentEventSource {
  static instances: SilentEventSource[] = []
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  constructor() { SilentEventSource.instances.push(this) }
  emit(data: object) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent)
  }
}

const { getJudgmentStatus, postJudgmentStart, postJudgmentStop, getStock } = vi.hoisted(() => ({
  getJudgmentStatus: vi.fn(),
  postJudgmentStart: vi.fn(),
  postJudgmentStop: vi.fn(),
  getStock: vi.fn(),
}))

vi.mock('../api/client', () => ({
  checkHealth: vi.fn().mockResolvedValue(true),
  getAuthStatus: vi.fn().mockResolvedValue({ state: 'authenticated', user: { discogs_username: 'test', is_admin: false } }),
  setUnauthorizedHandler: vi.fn(),
  getUserHiddenCrawlers: vi.fn().mockResolvedValue([]),
  postUserHiddenCrawlers: vi.fn().mockResolvedValue(undefined),
  refreshCollection: vi.fn().mockResolvedValue({ started: true, running: true }),
  getCollectionStatus: vi.fn().mockResolvedValue({ total: 0, last_synced: null, sync: null }),
  getCrawlStatus: vi.fn().mockResolvedValue({ total: 0, missing: 0, oldest_checked: null }),
  postCrawlStart: vi.fn().mockResolvedValue({ enqueued: 0 }),
  getCrawlers: vi.fn().mockResolvedValue([]),
  openCrawlStream: vi.fn(() => new SilentEventSource()),
  getReleases: vi.fn().mockResolvedValue({ total: 0, page: 1, per_page: 250, releases: [] }),
  getArtists: vi.fn().mockResolvedValue([]),
  getSettings: vi.fn().mockResolvedValue({
    crawl_delay_seconds: 30, consecutive_failure_limit: 10, crawl_schedule: '',
    crawl_schedule_mode: 'missing', ebay_app_id: '', ebay_cert_id: '', stock_schedule: '',
  }),
  getUserSettings: vi.fn().mockResolvedValue({
    anthropic_api_key: 'sk-ant-test', recommendation_item_limit: 300,
    plex_base_url: '', plex_token: '', plex_match_threshold: 90,
  }),
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
  getStock,
  getStockArtists: vi.fn().mockResolvedValue([]),
  postStockSyncStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  postJudgmentStart,
  postJudgmentStop,
  getJudgmentStatus,
  clearJudgments: vi.fn(),
  exportRecommendationsCsv: vi.fn(),
  importRecommendationsCsv: vi.fn(),
  getPriceStatus: vi.fn().mockResolvedValue({ any_price_paid: false }),
  getNotificationsUnread: vi.fn().mockResolvedValue({ unread: 0, latest_id: null }),
  markNotificationsRead: vi.fn().mockResolvedValue({ unread: 0, latest_id: null }),
  listInvites: vi.fn().mockResolvedValue([]),
  createInvite: vi.fn().mockResolvedValue({ code: '' }),
}))

function run(overrides: Partial<StockJudgmentRun> = {}): StockJudgmentRun {
  return {
    status: 'running', running: true, stale: false, judged: 40, total: 300,
    error: null, stop_requested: false, started_at: null, finished_at: null,
    ...overrides,
  }
}

// Longer than App's judgment poll interval, so one advance lands exactly one
// more read.
const PAST_ONE_POLL = 3500

async function openProfile() {
  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: /profile/i }))
  const description = await screen.findByText(/Evaluate unprocessed Store items for recommendation/)
  return description.closest('tr') as HTMLElement
}

beforeEach(() => {
  SilentEventSource.instances = []
  vi.clearAllMocks()
  localStorage.clear()
  getJudgmentStatus.mockResolvedValue({ any_judged: false, run: null })
  getStock.mockResolvedValue({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] })
  postJudgmentStart.mockResolvedValue({ started: true, running: true, run: run({ judged: 0 }) })
  postJudgmentStop.mockResolvedValue({ stopping: true, run: run({ stop_requested: true }) })
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('stopping a recommendation run from the profile page', () => {
  it('reads Refresh when no run is under way', async () => {
    const row = await openProfile()
    expect(within(row).getByRole('button')).toHaveTextContent('Refresh')
  })

  it('flips to Stop on the start response, without any event arriving', async () => {
    const row = await openProfile()
    fireEvent.click(within(row).getByRole('button'))

    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))
    expect(SilentEventSource.instances.length).toBeGreaterThan(0)
    expect(postJudgmentStart).toHaveBeenCalledTimes(1)
  })

  it('shows Stop for a run already under way at mount', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: run() })
    const row = await openProfile()
    // A reload mid-run, or a run this tab never started -- its own click is
    // not what puts the button in this state, the row is.
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))
    expect(postJudgmentStart).not.toHaveBeenCalled()
  })

  it('asks the run to stop and reads Stopping… until it actually ends', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: run() })
    const row = await openProfile()
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))

    // The run stays running with the flag set: it finishes the batch already
    // paid for before it closes, and that window is what Stopping… covers.
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: run({ stop_requested: true }) })
    fireEvent.click(within(row).getByRole('button'))

    await waitFor(() => expect(postJudgmentStop).toHaveBeenCalledTimes(1))
    expect(postJudgmentStart).not.toHaveBeenCalled()
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stopping…'))
    expect(within(row).getByRole('button')).toBeDisabled()
    await screen.findByText(/Stopping the recommendation run/)

    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL)
    expect(within(row).getByRole('button')).toHaveTextContent('Stopping…')
  })

  it('returns to Refresh when the poll finds the run stopped', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: run() })
    const row = await openProfile()
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))

    getJudgmentStatus.mockResolvedValue({
      any_judged: true,
      run: run({ status: 'stopped', running: false, stop_requested: true, judged: 80 }),
    })
    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL)

    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Refresh'))
    expect(within(row).getByRole('button')).not.toBeDisabled()
  })

  it('returns to Refresh when a stock_judgment_stopped event arrives', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: run() })
    const row = await openProfile()
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))

    // The same-Machine fast path: this browser did hear the ending, so it does
    // not wait for the next poll.
    getJudgmentStatus.mockResolvedValue({
      any_judged: true,
      run: run({ status: 'stopped', running: false, judged: 80 }),
    })
    SilentEventSource.instances[0].emit({
      status: 'stock_judgment_stopped', judged: 80, total: 300, id: 1,
    })

    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Refresh'))
    await screen.findByText('Recommendation run stopped — 80 of 300 items checked')
  })

  it('keeps Stopping… when a started event is delivered after the stop click', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: run() })
    const row = await openProfile()
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))

    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: run({ stop_requested: true }) })
    fireEvent.click(within(row).getByRole('button'))
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stopping…'))

    // Queued before the click, delivered after it. Clearing the optimistic
    // stop here would re-enable the button over a row whose flag is set,
    // inviting a second click that does nothing.
    //
    // Asserted on the flush of the event itself, with no poll allowed to run
    // in between: the poll would put "Stopping…" back from the row a moment
    // later, so a test that waited for it would pass against the bug.
    await act(async () => {
      SilentEventSource.instances[0].emit({ status: 'stock_judgment_started', id: 2 })
    })
    expect(within(row).getByRole('button')).toHaveTextContent('Stopping…')
    expect(within(row).getByRole('button')).toBeDisabled()
  })

  it('restores Stop when a terminal event is contradicted by the row', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: run() })
    const row = await openProfile()
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))

    // A judgment event names no run, so an ending delivered late is
    // indistinguishable from the current run's -- and a newer run started on
    // the other Machine is exactly when acting on it takes Stop away from a
    // run that is still spending. The row gets the last word.
    const readsBefore = getJudgmentStatus.mock.calls.length
    await act(async () => {
      SilentEventSource.instances[0].emit({
        status: 'stock_judgment_complete', judged: 300, id: 3,
      })
    })
    // The confirming read is the fix, so it is what this asserts on. Without
    // it the flags stay cleared and the poll is torn down with them, so no
    // further read happens at all and the button sits on Refresh.
    await waitFor(() => expect(getJudgmentStatus.mock.calls.length).toBeGreaterThan(readsBefore))
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))
  })

  it('refetches the Store when the poll sees the run advance', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: run({ judged: 40 }) })
    const row = await openProfile()
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))
    fireEvent.click(await screen.findByRole('button', { name: /store/i }))
    await waitFor(() => expect(getStock).toHaveBeenCalled())
    const beforeAdvance = getStock.mock.calls.length

    // On the Machine that is not running the job, no stock_judgment_* event
    // ever arrives, so the poll's own observation is the only thing that can
    // tell an open Store tab there are new judgments to show.
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: run({ judged: 80 }) })
    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL)

    await waitFor(() => expect(getStock.mock.calls.length).toBeGreaterThan(beforeAdvance))
  })

  it('refetches the Store for a run that began and ended between two reads', async () => {
    // Mount with no run at all, which is what a tab that was open before the
    // run started sees. Leaving that read unrecorded made the *next* one look
    // like the first, so a short run on the other Machine finished without
    // ever refetching this tab.
    getJudgmentStatus.mockResolvedValue({ any_judged: false, run: null })
    const row = await openProfile()
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Refresh'))
    fireEvent.click(await screen.findByRole('button', { name: /store/i }))
    await waitFor(() => expect(getStock).toHaveBeenCalled())
    const beforeRun = getStock.mock.calls.length

    getJudgmentStatus.mockResolvedValue({
      any_judged: true,
      run: run({ status: 'complete', running: false, judged: 40 }),
    })
    // Nothing is polling -- no run was believed to be in flight -- so the start
    // click is what sets the poll going, and its first tick is the read that
    // discovers the run.
    fireEvent.click(await screen.findByRole('button', { name: /profile/i }))
    fireEvent.click(within(row).getByRole('button'))
    await waitFor(() => expect(postJudgmentStart).toHaveBeenCalled())
    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL)

    await waitFor(() => expect(getStock.mock.calls.length).toBeGreaterThan(beforeRun))
  })

  it('ignores a start response whose snapshot predates a stop click', async () => {
    // The POST is still in flight when the worker announces the run and the
    // user clicks Stop, so its reply describes a moment before both. Letting
    // it write would turn the disabled "Stopping…" back into "Stop".
    let releaseStart: (v: unknown) => void = () => {}
    postJudgmentStart.mockReturnValue(new Promise((resolve) => {
      releaseStart = () => resolve({ started: true, running: true, run: run({ judged: 0 }) })
    }))
    getJudgmentStatus.mockResolvedValue({ any_judged: false, run: null })

    const row = await openProfile()
    fireEvent.click(within(row).getByRole('button'))
    await waitFor(() => expect(postJudgmentStart).toHaveBeenCalled())

    // The run announces itself, and the user stops it, both before the start
    // reply lands.
    await act(async () => {
      SilentEventSource.instances[0].emit({ status: 'stock_judgment_started', id: 4 })
    })
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: run({ stop_requested: true }) })
    postJudgmentStop.mockResolvedValue({ stopping: true, run: run({ stop_requested: true }) })
    fireEvent.click(within(row).getByRole('button'))
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stopping…'))

    await act(async () => { releaseStart(undefined) })
    expect(within(row).getByRole('button')).toHaveTextContent('Stopping…')
  })

  it('reports a run that had already finished rather than failing the click', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: run() })
    postJudgmentStop.mockResolvedValue({
      stopping: false, run: run({ status: 'complete', running: false, judged: 300 }),
    })
    const row = await openProfile()
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))

    getJudgmentStatus.mockResolvedValue({
      any_judged: true, run: run({ status: 'complete', running: false, judged: 300 }),
    })
    fireEvent.click(within(row).getByRole('button'))

    await screen.findByText(/No recommendation run to stop/)
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Refresh'))
  })

  it('refetches the Store when a new run ends identically to the last one', async () => {
    // Two runs can be indistinguishable by status and count alone. Without the
    // run's own identity in the key, the Store sits on the older one's
    // judgments.
    getJudgmentStatus.mockResolvedValue({
      any_judged: true,
      run: run({ status: 'complete', running: false, judged: 40, started_at: '2026-09-16T01:00:00' }),
    })
    const row = await openProfile()
    fireEvent.click(await screen.findByRole('button', { name: /store/i }))
    await waitFor(() => expect(getStock).toHaveBeenCalled())
    const beforeRun = getStock.mock.calls.length

    // A different run, same shape.
    getJudgmentStatus.mockResolvedValue({
      any_judged: true,
      run: run({ status: 'complete', running: false, judged: 40, started_at: '2026-09-16T02:00:00' }),
    })
    fireEvent.click(await screen.findByRole('button', { name: /profile/i }))
    fireEvent.click(within(row).getByRole('button'))
    await waitFor(() => expect(postJudgmentStart).toHaveBeenCalled())
    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL)

    await waitFor(() => expect(getStock.mock.calls.length).toBeGreaterThan(beforeRun))
  })

  it('follows the run when the start request fails but the claim was taken', async () => {
    // The server can commit the claim and create the task before the
    // connection drops. Assuming the start failed would leave a paid run
    // going with nothing polling it and the button reading Refresh.
    postJudgmentStart.mockRejectedValue(new Error('network'))
    getJudgmentStatus.mockResolvedValueOnce({ any_judged: false, run: null })
      .mockResolvedValue({ any_judged: false, run: run({ judged: 0 }) })

    const row = await openProfile()
    fireEvent.click(within(row).getByRole('button'))

    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))
  })

  it('says a stale run stopped responding rather than that it finished', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: run() })
    postJudgmentStop.mockResolvedValue({
      stopping: false,
      run: run({ running: false, stale: true }),
    })
    const row = await openProfile()
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))

    getJudgmentStatus.mockResolvedValue({
      any_judged: true, run: run({ running: false, stale: true }),
    })
    fireEvent.click(within(row).getByRole('button'))

    await screen.findByText(/stopped responding/)
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Refresh'))
  })

  it('keeps looking for a run when the mount-time read fails once', async () => {
    // On the Machine that is not running the job, this read is the only thing
    // that can discover a run at all -- no event arrives, and the poll only
    // starts once one is believed to be in flight. A single dropped request
    // would hide a whole paid run behind a Refresh button.
    getJudgmentStatus.mockRejectedValueOnce(new Error('network'))
      .mockResolvedValue({ any_judged: false, run: run({ judged: 40 }) })

    const row = await openProfile()
    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL)

    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))
    expect(postJudgmentStart).not.toHaveBeenCalled()
  })

  it('does not let a failed start response talk over a newer stop', async () => {
    // The rejection arrives after the run announced itself and the user
    // stopped it. Neither the message nor the recovery read may act on it: the
    // read's snapshot can predate the stop commit and would re-enable the
    // button.
    let rejectStart: (e: unknown) => void = () => {}
    postJudgmentStart.mockReturnValue(new Promise((_resolve, reject) => {
      rejectStart = () => reject(new Error('network'))
    }))
    getJudgmentStatus.mockResolvedValue({ any_judged: false, run: null })

    const row = await openProfile()
    fireEvent.click(within(row).getByRole('button'))
    await waitFor(() => expect(postJudgmentStart).toHaveBeenCalled())

    await act(async () => {
      SilentEventSource.instances[0].emit({ status: 'stock_judgment_started', id: 5 })
    })
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: run({ stop_requested: true }) })
    postJudgmentStop.mockResolvedValue({ stopping: true, run: run({ stop_requested: true }) })
    fireEvent.click(within(row).getByRole('button'))
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stopping…'))

    await act(async () => { rejectStart(undefined) })
    expect(within(row).getByRole('button')).toHaveTextContent('Stopping…')
    expect(screen.queryByText(/failed to start/)).not.toBeInTheDocument()
  })

  it('keeps looking when the first read after a failed start says no run yet', async () => {
    // The POST can fail at the client while the server is still committing the
    // claim, so the immediate read can win that race and answer about a run
    // that is about to exist. Stopping there would hide it for good.
    postJudgmentStart.mockRejectedValue(new Error('network'))
    getJudgmentStatus.mockResolvedValueOnce({ any_judged: false, run: null })
      .mockResolvedValueOnce({ any_judged: false, run: null })
      .mockResolvedValue({ any_judged: false, run: run({ judged: 0 }) })

    const row = await openProfile()
    fireEvent.click(within(row).getByRole('button'))
    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL * 2)

    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))
  })

  it('does not report a failed start once recovery finds the run running', async () => {
    // "Failed to start" beside a live Stop button is a contradiction, and the
    // recovery is what resolves it -- so the verdict waits for it.
    postJudgmentStart.mockRejectedValue(new Error('network'))
    getJudgmentStatus.mockResolvedValueOnce({ any_judged: false, run: null })
      .mockResolvedValue({ any_judged: false, run: run({ judged: 0 }) })

    const row = await openProfile()
    fireEvent.click(within(row).getByRole('button'))
    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL)

    await screen.findByText(/the recommendation run did start/)
    expect(screen.queryByText(/failed to start: network/)).not.toBeInTheDocument()
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))
  })

  it('still reports a start that really did fail', async () => {
    postJudgmentStart.mockRejectedValue(new Error('network'))
    getJudgmentStatus.mockResolvedValue({ any_judged: false, run: null })

    const row = await openProfile()
    fireEvent.click(within(row).getByRole('button'))
    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL * 3)

    await screen.findByText(/failed to start: network/)
    expect(within(row).getByRole('button')).toHaveTextContent('Refresh')
  })

  it('keeps Stopping… when a poll lands mid-request with the pre-stop row', async () => {
    // The poll can tick while the stop POST is in flight and read the row
    // before the flag commits. Acting on that answer re-enables the button
    // over a stop that is already on its way -- and then discards the stop's
    // own reply for being older than the read.
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: run() })
    let releaseStop: (v: unknown) => void = () => {}
    postJudgmentStop.mockReturnValue(new Promise((resolve) => {
      releaseStop = () => resolve({ stopping: true, run: run({ stop_requested: true }) })
    }))

    const row = await openProfile()
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))
    fireEvent.click(within(row).getByRole('button'))
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stopping…'))

    // A poll tick, still answering from before the stop committed.
    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL)
    expect(within(row).getByRole('button')).toHaveTextContent('Stopping…')
    expect(within(row).getByRole('button')).toBeDisabled()

    await act(async () => { releaseStop(undefined) })
    expect(within(row).getByRole('button')).toHaveTextContent('Stopping…')
  })

  it('says so when a start is refused by a run already under way', async () => {
    postJudgmentStart.mockResolvedValue({ started: false, running: true, run: run() })
    const row = await openProfile()
    fireEvent.click(within(row).getByRole('button'))

    await screen.findByText(/A recommendation run is already under way/)
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))
  })

  it('does not poll the run once nothing is running', async () => {
    await openProfile()
    const atRest = getJudgmentStatus.mock.calls.length
    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL * 3)
    expect(getJudgmentStatus.mock.calls.length).toBe(atRest)
  })
})
