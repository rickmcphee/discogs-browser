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

const { getJudgmentStatus, postJudgmentStart, postJudgmentStop, getStock, clearJudgments } = vi.hoisted(() => ({
  getJudgmentStatus: vi.fn(),
  postJudgmentStart: vi.fn(),
  postJudgmentStop: vi.fn(),
  getStock: vi.fn(),
  clearJudgments: vi.fn(),
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
  clearJudgments,
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
  clearJudgments.mockResolvedValue({ cleared: true, count: 7 })
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

  // A stopped run has still fanned its verdicts out over the listings of the
  // records it judged before the stop, and those are as much "not paid for
  // again" as the judged ones. (Merge of PR #367 into PR #368.)
  it('reports what a stopped run inherited, and unlocks Recommended on it alone', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: false, run: run() })
    const row = await openProfile()
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))

    getJudgmentStatus.mockResolvedValue({
      any_judged: true,
      run: run({ status: 'stopped', running: false, judged: 0 }),
    })
    SilentEventSource.instances[0].emit({
      status: 'stock_judgment_stopped', judged: 0, total: 300, inherited: 12, id: 1,
    })

    await screen.findByText(
      'Recommendation run stopped — 0 of 300 items checked, 12 matched to records already judged',
    )
    // Judged nothing, yet wrote rows -- so Export must not still be disabled.
    await waitFor(() =>
      expect(screen.getByText('Export').closest('button')).not.toBeDisabled(),
    )
  })

  // A terminal event names no run and can be replayed -- this Machine's SSE
  // buffer resends it on reconnect -- so one can land after a Clear has
  // removed every judgment. Its counts are then true of a run that happened
  // and false of the database, and the authoritative read is the only thing
  // that can tell the difference. The handler's own comment says that read
  // "gets the last word"; it only does if the optimistic write happens first,
  // since the writer that bumps the sequence last is the one that wins.
  // (Copilot, PR #368, round 23.)
  it('lets the status read overrule a replayed ending that lands after a clear', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: null })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const row = await openProfile()
    await waitFor(() =>
      expect(screen.getByText('Export').closest('button')).not.toBeDisabled(),
    )

    // Everything is gone, and the server says so from here on.
    getJudgmentStatus.mockResolvedValue({ any_judged: false, run: null })
    fireEvent.click(within(row.closest('table') as HTMLElement).getByRole('button', { name: 'Clear' }))
    await waitFor(() =>
      expect(screen.getByText('Export').closest('button')).toBeDisabled(),
    )

    // Replayed now, carrying counts from the run that preceded the clear.
    await act(async () => {
      SilentEventSource.instances[0].emit({
        status: 'stock_judgment_complete', judged: 0, total: 300, inherited: 12, id: 9,
      })
    })

    await waitFor(() =>
      expect(screen.getByText('Export').closest('button')).toBeDisabled(),
    )
  })

  // And the read has to actually land. refreshJudgmentStatus swallows a failed
  // request into null, the terminal handler has already stopped the run poll,
  // and nothing else is coming on the Machine that did not run the job -- so a
  // single dropped read leaves the optimistic true standing for good, which is
  // the same empty-table state one round later. The bounded retry beside it
  // exists for exactly this ("A single attempt is not enough anywhere this is
  // called"). (Copilot, PR #368, round 24.)
  it('retries the status read after a replayed ending when the first request fails', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: null })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const row = await openProfile()
    await waitFor(() =>
      expect(screen.getByText('Export').closest('button')).not.toBeDisabled(),
    )

    getJudgmentStatus.mockResolvedValue({ any_judged: false, run: null })
    fireEvent.click(within(row.closest('table') as HTMLElement).getByRole('button', { name: 'Clear' }))
    await waitFor(() =>
      expect(screen.getByText('Export').closest('button')).toBeDisabled(),
    )

    // The ending's own read fails; only a second attempt can report the
    // cleared table.
    getJudgmentStatus.mockRejectedValueOnce(new Error('network'))
    await act(async () => {
      SilentEventSource.instances[0].emit({
        status: 'stock_judgment_complete', judged: 0, total: 300, inherited: 12, id: 9,
      })
    })

    await act(async () => { await vi.advanceTimersByTimeAsync(PAST_ONE_POLL) })
    await waitFor(() =>
      expect(screen.getByText('Export').closest('button')).toBeDisabled(),
    )
  })

  // The retry above spans seconds, and a user can act inside it. Each attempt
  // calls refreshJudgmentStatus, which bumps latestJudgmentRunSeq -- so a
  // retry landing after a Refresh click takes a token newer than the start
  // request's, reads the row before the claim commits, and the accepted start
  // response is thrown out by its own sequence check. On the Machine that does
  // not serve this browser's SSE nothing else corrects it, leaving a paid run
  // behind a button that still says Refresh. (Copilot, PR #368, round 25.)
  it('abandons the retry when the user starts a run inside it', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: null })
    const row = await openProfile()
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Refresh'))

    // The ending's first read fails, so the retry is asleep.
    getJudgmentStatus.mockRejectedValueOnce(new Error('network'))
    await act(async () => {
      SilentEventSource.instances[0].emit({
        status: 'stock_judgment_complete', judged: 12, total: 300, id: 9,
      })
    })

    // The start is held open, so the retry fires while the claim is still
    // uncommitted and the row it reads still says idle.
    let acceptStart: (v: unknown) => void = () => {}
    postJudgmentStart.mockImplementationOnce(() => new Promise((resolve) => { acceptStart = resolve }))
    fireEvent.click(within(row).getByRole('button'))
    await act(async () => { await vi.advanceTimersByTimeAsync(PAST_ONE_POLL) })

    // The claim lands. Its response is the only thing that can flip the button,
    // and a retry that bumped the read counter past it throws it away.
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: run() })
    await act(async () => {
      acceptStart({ started: true, running: true, run: run() })
    })

    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))
  })

  // start_judgment_only reads the guards and the router then reads the row for
  // `running`, so a run that refused this start can finish in between and
  // leave every flag false. Both named branches miss it, and the click passes
  // in silence -- which is what those branches were added to end.
  // (Copilot, PR #368, round 27.)
  it('says something when a start is refused and the reason has already gone', async () => {
    const row = await openProfile()
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Refresh'))

    postJudgmentStart.mockResolvedValueOnce({
      started: false, running: false, stock_sync_running: false, run: null,
    })
    fireEvent.click(within(row).getByRole('button'))

    await screen.findByText('Recommendations did not start — try Refresh again.')
  })

  // A replayed ending can land while a Refresh POST is still short of its
  // claim commit. The ending clears the run flags and bumps the run sequence,
  // so the start's own reply -- the only thing that can flip the button, and
  // authoritative about the claim it just made -- fails its sequence check and
  // is thrown away. Where this browser's SSE is served by the other Machine no
  // event ever corrects it, and a paid run hides behind Refresh.
  // (Copilot, PR #368, round 28.)
  it('does not let a replayed ending cancel a start that is still in flight', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: null })
    const row = await openProfile()
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Refresh'))

    let acceptStart: (v: unknown) => void = () => {}
    postJudgmentStart.mockImplementationOnce(() => new Promise((resolve) => { acceptStart = resolve }))
    fireEvent.click(within(row).getByRole('button'))

    // Replayed while the claim is uncommitted, so every read still says idle.
    await act(async () => {
      SilentEventSource.instances[0].emit({
        status: 'stock_judgment_complete', judged: 12, total: 300, id: 9,
      })
    })

    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: run() })
    await act(async () => {
      acceptStart({ started: true, running: true, run: run() })
    })

    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))
  })

  // The other half of letting a start own the run state: the ending's own
  // reconciliation is dropped, not deferred. If that ending is a replay after
  // a Clear it has already set hasJudgedItems optimistically, and when the
  // start then comes back refused -- a stock sync blocked it, say -- no run
  // poll starts and nothing is left to read `any_judged: false` back.
  // Recommended and Export stay enabled over an empty table, which is rounds
  // 23 and 24 again. (Copilot, PR #368, round 29.)
  it('reconciles after a refused start that swallowed an ending mid-flight', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: null })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const row = await openProfile()
    await waitFor(() =>
      expect(screen.getByText('Export').closest('button')).not.toBeDisabled(),
    )

    getJudgmentStatus.mockResolvedValue({ any_judged: false, run: null })
    fireEvent.click(within(row.closest('table') as HTMLElement).getByRole('button', { name: 'Clear' }))
    await waitFor(() =>
      expect(screen.getByText('Export').closest('button')).toBeDisabled(),
    )

    let settleStart: (v: unknown) => void = () => {}
    postJudgmentStart.mockImplementationOnce(() => new Promise((resolve) => { settleStart = resolve }))
    fireEvent.click(within(row).getByRole('button'))

    // Replayed inside the start, so the ending defers to it and reconciles
    // nothing of its own.
    await act(async () => {
      SilentEventSource.instances[0].emit({
        status: 'stock_judgment_complete', judged: 0, total: 300, inherited: 12, id: 9,
      })
    })

    // And the start is refused, so no run poll follows it either.
    await act(async () => {
      settleStart({ started: false, running: false, stock_sync_running: true, run: null })
    })

    await waitFor(() =>
      expect(screen.getByText('Export').closest('button')).toBeDisabled(),
    )
  })

  // The start-pending flag was released only by the newest *action*, copied
  // from the stop's own rule -- where it is right, because a newer stop raises
  // the flag again. A newer Stop does not raise the start flag, so when one
  // supersedes an in-flight start nothing ever lowers it: it stays true for
  // the life of the page, and every later ending skips its run-state cleanup
  // and its reconciliation. (Copilot, PR #368, round 30.)
  it('releases the start-pending flag when a stop supersedes the start', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: null })
    const row = await openProfile()
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Refresh'))

    let settleStart: (v: unknown) => void = () => {}
    postJudgmentStart.mockImplementationOnce(() => new Promise((resolve) => { settleStart = resolve }))
    fireEvent.click(within(row).getByRole('button'))

    // The run announces itself, so the button offers Stop while the POST that
    // started it is still open.
    await act(async () => {
      SilentEventSource.instances[0].emit({ status: 'stock_judgment_started', id: 1 })
    })
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))

    // Stop supersedes the start, taking the action counter with it.
    fireEvent.click(within(row).getByRole('button'))
    await waitFor(() => expect(postJudgmentStop).toHaveBeenCalled())
    await act(async () => {
      settleStart({ started: true, running: true, run: run() })
    })

    // The ending must still be able to put the button back.
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: null })
    await act(async () => {
      SilentEventSource.instances[0].emit({
        status: 'stock_judgment_complete', judged: 40, total: 40, id: 1,
      })
    })
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Refresh'))
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

  // The start-in-flight guard covered the run flags and not the presentation,
  // so a replayed ending still turned the spinner off and wrote "Finished…"
  // over a run the reply was about to confirm as live. On the Machine not
  // running the job nothing else arrives, so that reads as finished for the
  // whole run. (Copilot, PR #368, round 34.)
  it('shows the run as live when the start reply contradicts a replayed ending', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: null })
    const row = await openProfile()
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Refresh'))

    let acceptStart: (v: unknown) => void = () => {}
    postJudgmentStart.mockImplementationOnce(() => new Promise((resolve) => { acceptStart = resolve }))
    fireEvent.click(within(row).getByRole('button'))

    await act(async () => {
      SilentEventSource.instances[0].emit({
        status: 'stock_judgment_complete', judged: 12, total: 300, id: 9,
      })
    })
    expect(screen.getByText(/Finished finding recommendations — 12 items checked/)).toBeInTheDocument()

    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: run() })
    await act(async () => {
      acceptStart({ started: true, running: true, run: run() })
    })

    await waitFor(() =>
      expect(screen.getByText(/Finding recommendations for Store items…/)).toBeInTheDocument(),
    )
    expect(screen.queryByText(/Finished finding recommendations/)).not.toBeInTheDocument()
  })

  // And the same ending replayed with no start in flight. The row already
  // gets the last word on the flags; it has to get it on the spinner and the
  // banner too, or the button says Stop beside a message saying it finished.
  // (Copilot, PR #368, round 34.)
  it('restores the running banner when a terminal event is contradicted by the row', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: run() })
    const row = await openProfile()
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))

    await act(async () => {
      SilentEventSource.instances[0].emit({
        status: 'stock_judgment_complete', judged: 300, total: 300, id: 3,
      })
    })

    await waitFor(() =>
      expect(screen.getByText(/Finding recommendations for Store items…/)).toBeInTheDocument(),
    )
    expect(screen.queryByText(/Finished finding recommendations/)).not.toBeInTheDocument()
  })

  // And the error ending, which reconciles the flags exactly as the two
  // terminal ones do but did not follow them in restoring the presentation.
  // An error names no run either, so a replayed one can be an older run's.
  // (Copilot, PR #368, round 35.)
  it('restores the running banner when an error event is contradicted by the row', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true, run: run() })
    const row = await openProfile()
    await waitFor(() => expect(within(row).getByRole('button')).toHaveTextContent('Stop'))

    await act(async () => {
      SilentEventSource.instances[0].emit({
        status: 'stock_judgment_error', error: 'boom', id: 4,
      })
    })

    await waitFor(() =>
      expect(screen.getByText(/Finding recommendations for Store items…/)).toBeInTheDocument(),
    )
    expect(screen.queryByText(/Finding recommendations failed/)).not.toBeInTheDocument()
  })

  it('does not poll the run once nothing is running', async () => {
    await openProfile()
    const atRest = getJudgmentStatus.mock.calls.length
    await vi.advanceTimersByTimeAsync(PAST_ONE_POLL * 3)
    expect(getJudgmentStatus.mock.calls.length).toBe(atRest)
  })
})
