import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
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

const { getJudgmentStatus, postJudgmentStart, postJudgmentStop } = vi.hoisted(() => ({
  getJudgmentStatus: vi.fn(),
  postJudgmentStart: vi.fn(),
  postJudgmentStop: vi.fn(),
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
  getStock: vi.fn().mockResolvedValue({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] }),
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
