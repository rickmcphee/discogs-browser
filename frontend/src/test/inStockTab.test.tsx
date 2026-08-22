import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import App from '../App'

// jsdom doesn't implement object URLs
window.URL.createObjectURL = vi.fn(() => 'blob:mock-url')
window.URL.revokeObjectURL = vi.fn()

class MockEventSource {
  static instances: MockEventSource[] = []
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  constructor() { MockEventSource.instances.push(this) }
  emit(data: object) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent)
  }
}

const postStockSyncStart = vi.fn().mockResolvedValue({ started: true, running: true })
const postJudgmentStart = vi.fn().mockResolvedValue({ started: true, running: true })
const clearJudgments = vi.fn()
const exportRecommendationsCsv = vi.fn()
const getSettings = vi.fn()
const getUserSettings = vi.fn()
const getJudgmentStatus = vi.fn()
const getPriceStatus = vi.fn()
const getCrawlers = vi.fn()
const getStock = vi.fn()
const postUserHiddenCrawlers = vi.fn().mockResolvedValue(undefined)
const getUserHiddenCrawlers = vi.fn()

vi.mock('../api/client', () => ({
  checkHealth: vi.fn().mockResolvedValue(true),
  getAuthStatus: vi.fn().mockResolvedValue({ state: 'authenticated', user: { discogs_username: 'test', is_admin: true } }),
  setUnauthorizedHandler: vi.fn(),
  getUserHiddenCrawlers: (...args: unknown[]) => getUserHiddenCrawlers(...args),
  postUserHiddenCrawlers: (...args: unknown[]) => postUserHiddenCrawlers(...args),
  refreshCollection: vi.fn().mockResolvedValue({ synced: 0, username: 'test' }),
  getCollectionStatus: vi.fn().mockResolvedValue({ total: 0, last_synced: null }),
  getCrawlStatus: vi.fn().mockResolvedValue({ total: 0, missing: 0, oldest_checked: null }),
  postCrawlStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  getCrawlers: (...args: unknown[]) => getCrawlers(...args),
  openCrawlStream: vi.fn(() => new MockEventSource()),
  getReleases: vi.fn().mockResolvedValue({ total: 0, page: 1, per_page: 50, releases: [] }),
  getArtists: vi.fn().mockResolvedValue([]),
  getSettings: (...args: unknown[]) => getSettings(...args),
  getUserSettings: (...args: unknown[]) => getUserSettings(...args),
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
  getStock: (...args: unknown[]) => getStock(...args),
  getStockArtists: vi.fn().mockResolvedValue([]),
  postStockSyncStart: (...args: unknown[]) => postStockSyncStart(...args),
  postJudgmentStart: (...args: unknown[]) => postJudgmentStart(...args),
  clearJudgments: (...args: unknown[]) => clearJudgments(...args),
  exportRecommendationsCsv: (...args: unknown[]) => exportRecommendationsCsv(...args),
  getJudgmentStatus: (...args: unknown[]) => getJudgmentStatus(...args),
  getPriceStatus: (...args: unknown[]) => getPriceStatus(...args),
  listInvites: vi.fn().mockResolvedValue([]),
  createInvite: vi.fn().mockResolvedValue({ code: '' }),
}))

function getLastCrawlSource() {
  return MockEventSource.instances[MockEventSource.instances.length - 1]
}

const defaultSettings = {
  crawl_delay_seconds: 30, consecutive_failure_limit: 10, crawl_schedule: '',
  crawl_schedule_mode: 'missing',
  ebay_app_id: '', ebay_cert_id: '', stock_schedule: '',
}

const defaultUserSettings = { anthropic_api_key: '', recommendation_item_limit: 300, plex_base_url: '', plex_token: '', plex_match_threshold: 90 }

beforeEach(() => {
  MockEventSource.instances = []
  vi.clearAllMocks()
  localStorage.clear()
  postStockSyncStart.mockResolvedValue({ started: true, running: true })
  postJudgmentStart.mockResolvedValue({ started: true, running: true })
  clearJudgments.mockResolvedValue({ cleared: true, running: false, count: 7 })
  exportRecommendationsCsv.mockResolvedValue(new Blob(['artist,title\n'], { type: 'text/csv' }))
  getSettings.mockResolvedValue(defaultSettings)
  getUserSettings.mockResolvedValue(defaultUserSettings)
  getJudgmentStatus.mockResolvedValue({ any_judged: false })
  getPriceStatus.mockResolvedValue({ any_price_paid: false })
  getCrawlers.mockResolvedValue([])
  getStock.mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] })
  postUserHiddenCrawlers.mockResolvedValue(undefined)
  getUserHiddenCrawlers.mockResolvedValue([])
})

describe('In Stock tab', () => {
  it('shows a Store nav button that switches views', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getByText('Store')).toBeInTheDocument())
    const storeButton = screen.getByText('Store')
    fireEvent.click(storeButton)
    await waitFor(() => expect(storeButton.className).toContain('bg-white'))
  })

  it('shows a Track nav button that switches to a track-scoped StockBrowser', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getByText('Track')).toBeInTheDocument())
    const trackButton = screen.getByText('Track')
    fireEvent.click(trackButton)
    await waitFor(() => expect(trackButton.className).toContain('bg-white'))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ libraryScope: 'all' })))
  })

  it('hides the Track Price column when the user has no collection price data', async () => {
    getPriceStatus.mockResolvedValue({ any_price_paid: false })
    render(<App />)
    await waitFor(() => expect(screen.getByText('Track')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Track'))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ libraryScope: 'all' })))
    expect(screen.queryByText(/Price/)).toBeNull()
  })

  it('renders a Price element somewhere when the user has collection price data (paired with the hides test above, which proves it is wired everywhere)', async () => {
    getPriceStatus.mockResolvedValue({ any_price_paid: true })
    render(<App />)
    await waitFor(() => expect(screen.getByText('Track')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Track'))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ libraryScope: 'all' })))
    // Collection/Wantlist RecordBrowser and the Store StockBrowser all stay mounted
    // alongside Track (only CSS-hidden), and Collection/Wantlist share the same
    // hasPriceData wiring, so "Price" legitimately matches more than once here.
    expect(screen.getAllByText(/Price/).length).toBeGreaterThan(0)
  })

  it('refetches price status after a non-wishlist sync completes', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    await waitFor(() => expect(getPriceStatus).toHaveBeenCalled())
    getLastCrawlSource().emit({ status: 'sync_complete', synced: 42, wishlist_synced: 7, username: 'alice', id: 1 })
    await waitFor(() => expect(getPriceStatus.mock.calls.length).toBeGreaterThan(1))
  })

  it('refetches price status after a sync fails partway through', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    await waitFor(() => expect(getPriceStatus).toHaveBeenCalled())
    getLastCrawlSource().emit({ status: 'sync_error', error: 'boom', id: 1 })
    await waitFor(() => expect(getPriceStatus.mock.calls.length).toBeGreaterThan(1))
  })

  it('does not let a slow bootstrap price-status response overwrite a newer post-sync one', async () => {
    let resolveBootstrap: (v: { any_price_paid: boolean }) => void = () => {}
    getPriceStatus
      .mockImplementationOnce(() => new Promise((resolve) => { resolveBootstrap = resolve }))
      .mockResolvedValueOnce({ any_price_paid: true })

    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    await waitFor(() => expect(getPriceStatus).toHaveBeenCalledTimes(1))

    getLastCrawlSource().emit({ status: 'sync_complete', synced: 1, wishlist_synced: null, username: 'alice', id: 1 })
    await waitFor(() => expect(getPriceStatus).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.getAllByText(/Price/).length).toBeGreaterThan(0))

    resolveBootstrap({ any_price_paid: false })
    await new Promise((r) => setTimeout(r, 0))
    expect(screen.getAllByText(/Price/).length).toBeGreaterThan(0)
  })

  it('calls postStockSyncStart when Refresh is clicked in Settings', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    const description = await screen.findByText('Scan all enabled catalog crawlers immediately.')
    const row = description.closest('tr') as HTMLElement
    fireEvent.click(within(row).getByText('Refresh'))
    await waitFor(() => expect(postStockSyncStart).toHaveBeenCalled())
  })

  const CATALOG_CRAWLER = { id: 9, site_name: 'Epitaph', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: null }

  it('calls postStockSyncStart with that crawler\'s id when its per-row Refresh button is clicked', async () => {
    getCrawlers.mockResolvedValue([CATALOG_CRAWLER])
    render(<App />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    const button = await screen.findByTitle('Refresh Epitaph catalog now')
    fireEvent.click(button)
    await waitFor(() => expect(postStockSyncStart).toHaveBeenCalledWith(9))
  })

  it('disables the bulk Refresh and every per-row Refresh button once a stock sync starts', async () => {
    getCrawlers.mockResolvedValue([CATALOG_CRAWLER])
    render(<App />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    const rowButton = await screen.findByTitle('Refresh Epitaph catalog now')
    const description = await screen.findByText('Scan all enabled catalog crawlers immediately.')
    const bulkButton = within(description.closest('tr') as HTMLElement).getByText('Refresh')

    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    getLastCrawlSource().emit({ status: 'stock_sync_started', crawler_id: 9, id: 1 })

    await waitFor(() => expect(rowButton).toBeDisabled())
    expect(bulkButton).toBeDisabled()
  })

  it('re-enables the buttons and stops spinning the row once the single-crawler sync completes', async () => {
    getCrawlers.mockResolvedValue([CATALOG_CRAWLER])
    render(<App />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    const rowButton = await screen.findByTitle('Refresh Epitaph catalog now')

    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_sync_started', crawler_id: 9, id: 1 })
    await waitFor(() => expect(rowButton).toBeDisabled())

    source.emit({ status: 'stock_sync_complete', synced: 5, crawler_id: 9, id: 2 })
    await waitFor(() => expect(rowButton).not.toBeDisabled())
  })

  it('surfaces stock_sync_progress events in the bottom status bar', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_sync_progress', synced: 3, source: 'Nuclear Blast', id: 1 })
    await waitFor(() => expect(screen.getByText(/Syncing in-stock catalog… 3 items \(Nuclear Blast\)/)).toBeInTheDocument())
  })

  it('names the store in the bottom status bar when its crawl starts', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_sync_source_started', source: 'The Sound Garden', id: 1 })
    await waitFor(() => expect(screen.getByText(/Syncing in-stock catalog… The Sound Garden/)).toBeInTheDocument())
  })

  it('surfaces per-page stock_sync_page_fetched progress in the bottom status bar', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_sync_page_fetched', source: 'The Sound Garden', page: 1, page_count: 250, id: 1 })
    await waitFor(() =>
      expect(
        screen.getByText(/Syncing in-stock catalog… The Sound Garden fetched page 1, 250 products/)
      ).toBeInTheDocument()
    )
  })

  it('says "1 product", not "1 products", on a single-product page', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_sync_page_fetched', source: 'Relapse', page: 9, page_count: 1, id: 1 })
    await waitFor(() =>
      expect(screen.getByText(/Relapse fetched page 9, 1 product$/)).toBeInTheDocument()
    )
  })

  it('surfaces stock_sync_complete events in the bottom status bar', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_sync_complete', synced: 12, id: 1 })
    await waitFor(() => expect(screen.getByText(/In-stock sync complete: 12 items/)).toBeInTheDocument())
  })

  it('surfaces stock_sync_aborted events in the bottom status bar and stops syncing', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_sync_started', id: 1 })
    await waitFor(() => expect(screen.getByText(/Syncing in-stock catalog…/)).toBeInTheDocument())
    source.emit({
      status: 'stock_sync_aborted',
      error: 'Too many consecutive rate-limited catalog sites',
      sources: ['Run For Cover', 'Equal Vision'],
      id: 2,
    })
    await waitFor(() =>
      expect(
        screen.getByText(/In-stock sync stopped: Too many consecutive rate-limited catalog sites \(Run For Cover, Equal Vision\)/)
      ).toBeInTheDocument()
    )
    expect(screen.getByRole('button', { name: /Dismiss/i })).toBeInTheDocument()
  })

  it('does not clear stockSyncTarget when a non-terminal per-crawler stock_sync_error fires mid-bulk-sync', async () => {
    getCrawlers.mockResolvedValue([CATALOG_CRAWLER])
    render(<App />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    const rowButton = await screen.findByTitle('Refresh Epitaph catalog now')
    const description = await screen.findByText('Scan all enabled catalog crawlers immediately.')
    const bulkButton = within(description.closest('tr') as HTMLElement).getByText('Refresh')

    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_sync_started', id: 1 })
    await waitFor(() => expect(rowButton).toBeDisabled())

    // Non-terminal: a single crawler failed mid-bulk-sync, but the sync
    // continues (carries "source", the discriminator for "not terminal").
    source.emit({ status: 'stock_sync_error', error: 'boom', source: 'Some Other Site', id: 2 })
    expect(rowButton).toBeDisabled()
    expect(bulkButton).toBeDisabled()

    // Terminal: the bulk sync actually finishes afterward.
    source.emit({ status: 'stock_sync_complete', synced: 5, id: 3 })
    await waitFor(() => expect(rowButton).not.toBeDisabled())
  })

  it('does not resurrect a dismissed in-stock sync message when a refresh replays the same buffered event', async () => {
    const { unmount } = render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    getLastCrawlSource().emit({ status: 'stock_sync_complete', synced: 12, id: 1 })
    await waitFor(() => expect(screen.getByText(/In-stock sync complete: 12 items/)).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: /Dismiss/i }))
    expect(screen.queryByText(/In-stock sync complete: 12 items/)).not.toBeInTheDocument()
    unmount()

    // A browser refresh remounts the app and opens a fresh SSE connection, which
    // replays every buffered event — including the one just dismissed.
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    getLastCrawlSource().emit({ status: 'stock_sync_complete', synced: 12, id: 1 })

    await waitFor(() => expect(screen.getByText('Store')).toBeInTheDocument())
    expect(screen.queryByText(/In-stock sync complete: 12 items/)).not.toBeInTheDocument()
  })

  it('surfaces stock_judgment_started events in the bottom status bar', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_judgment_started' })
    await waitFor(() => expect(screen.getByText(/Finding recommendations for Store items…/)).toBeInTheDocument())
  })

  it('surfaces stock_judgment_progress events in the bottom status bar', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_judgment_progress', judged: 5, total: 40, id: 1 })
    await waitFor(() => expect(screen.getByText(/Finding recommendations for Store items… 5\/40/)).toBeInTheDocument())
  })

  it('surfaces stock_judgment_complete events in the bottom status bar', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_judgment_complete', judged: 12, id: 1 })
    await waitFor(() => expect(screen.getByText(/Finished finding recommendations — 12 items checked/)).toBeInTheDocument())
  })

  it('surfaces stock_judgment_error events in the bottom status bar', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_judgment_error', error: 'boom', id: 1 })
    await waitFor(() => expect(screen.getByText(/Finding recommendations failed: boom/)).toBeInTheDocument())
  })

  it('calls postJudgmentStart when Refresh is clicked in the Account Recommendations section', async () => {
    getUserSettings.mockResolvedValue({ ...defaultUserSettings, anthropic_api_key: 'sk-ant-test' })
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /profile/i }))
    const description = await screen.findByText('Evaluate unprocessed Store items for recommendation, without a full catalog re-crawl.')
    const row = description.closest('tr') as HTMLElement
    fireEvent.click(within(row).getByText('Refresh'))
    await waitFor(() => expect(postJudgmentStart).toHaveBeenCalled())
  })

  it('disables Export until a judgment has completed', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /profile/i }))
    await waitFor(() => expect(screen.getByText('Export')).toBeInTheDocument())
    expect(screen.getByText('Export').closest('button')).toBeDisabled()
  })

  it('calls exportRecommendationsCsv when Export is clicked', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true })
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /profile/i }))
    await waitFor(() => expect(screen.getByText('Export').closest('button')).not.toBeDisabled())
    fireEvent.click(screen.getByText('Export'))
    await waitFor(() => expect(exportRecommendationsCsv).toHaveBeenCalled())
  })

  it('disables Clear until a judgment has completed', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /profile/i }))
    const description = await screen.findByText('Remove all recommendation judgments, recommended and not-recommended, so every Store item is re-evaluated from scratch on the next run.')
    const row = description.closest('tr') as HTMLElement
    expect(within(row).getByText('Clear').closest('button')).toBeDisabled()
  })

  it('does not call clearJudgments when the confirm dialog is cancelled', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true })
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /profile/i }))
    const description = await screen.findByText('Remove all recommendation judgments, recommended and not-recommended, so every Store item is re-evaluated from scratch on the next run.')
    const row = description.closest('tr') as HTMLElement
    await waitFor(() => expect(within(row).getByText('Clear').closest('button')).not.toBeDisabled())
    fireEvent.click(within(row).getByText('Clear'))
    expect(window.confirm).toHaveBeenCalled()
    expect(clearJudgments).not.toHaveBeenCalled()
  })

  it('calls clearJudgments and reports the count when confirmed', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /profile/i }))
    const description = await screen.findByText('Remove all recommendation judgments, recommended and not-recommended, so every Store item is re-evaluated from scratch on the next run.')
    const row = description.closest('tr') as HTMLElement
    await waitFor(() => expect(within(row).getByText('Clear').closest('button')).not.toBeDisabled())
    fireEvent.click(within(row).getByText('Clear'))
    await waitFor(() => expect(clearJudgments).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByText(/Cleared 7 recommendation judgments/)).toBeInTheDocument())
  })

  it('surfaces the running message when clear is refused because a run is in progress', async () => {
    getJudgmentStatus.mockResolvedValue({ any_judged: true })
    clearJudgments.mockResolvedValue({ cleared: false, running: true })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /profile/i }))
    const description = await screen.findByText('Remove all recommendation judgments, recommended and not-recommended, so every Store item is re-evaluated from scratch on the next run.')
    const row = description.closest('tr') as HTMLElement
    await waitFor(() => expect(within(row).getByText('Clear').closest('button')).not.toBeDisabled())
    fireEvent.click(within(row).getByText('Clear'))
    await waitFor(() => expect(screen.getByText(/Cannot clear recommendations while a sync or recommendation run is in progress/)).toBeInTheDocument())
  })

  it('enables Recommended in Store only once a key is configured and a judgment has completed', async () => {
    getUserSettings.mockResolvedValue({ ...defaultUserSettings, anthropic_api_key: 'sk-ant-test' })
    getJudgmentStatus.mockResolvedValue({ any_judged: true })
    render(<App />)
    await waitFor(() => expect(screen.getByText('Store')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Store'))
    await waitFor(() => {
      const option = screen.getByRole('option', { name: 'Recommended' }) as HTMLOptionElement
      expect(option.disabled).toBe(false)
    })
  })

  it('keeps Recommended enabled in Store while a judgment run is in progress', async () => {
    getUserSettings.mockResolvedValue({ ...defaultUserSettings, anthropic_api_key: 'sk-ant-test' })
    getJudgmentStatus.mockResolvedValue({ any_judged: true })
    render(<App />)
    await waitFor(() => expect(screen.getByText('Store')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Store'))
    await waitFor(() => expect((screen.getByRole('option', { name: 'Recommended' }) as HTMLOptionElement).disabled).toBe(false))
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_judgment_started' })
    await waitFor(() => expect((screen.getByRole('option', { name: 'Recommended' }) as HTMLOptionElement).disabled).toBe(false))
  })

  it('refetches stock items on a listing_changed SSE event', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    await waitFor(() => expect(getStock).toHaveBeenCalled())
    const callsBefore = getStock.mock.calls.length

    getLastCrawlSource().emit({ id: 1, type: 'listing_changed', status: 'found', discogs_id: 'r1', crawler_id: 9 })

    await waitFor(() => expect(getStock.mock.calls.length).toBeGreaterThan(callsBefore))
  })

  it('enables Recommended progressively on a first-ever run, as soon as the first batch lands', async () => {
    getUserSettings.mockResolvedValue({ ...defaultUserSettings, anthropic_api_key: 'sk-ant-test' })
    getJudgmentStatus.mockResolvedValue({ any_judged: false })
    render(<App />)
    await waitFor(() => expect(screen.getByText('Store')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Store'))
    await waitFor(() => expect((screen.getByRole('option', { name: 'Recommended' }) as HTMLOptionElement).disabled).toBe(true))
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_judgment_started' })
    source.emit({ status: 'stock_judgment_progress', judged: 40, total: 120, id: 1 })
    await waitFor(() => expect((screen.getByRole('option', { name: 'Recommended' }) as HTMLOptionElement).disabled).toBe(false))
  })

  it('refetches stock items on stock_judgment_progress and stock_judgment_complete SSE events', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    await waitFor(() => expect(getStock).toHaveBeenCalled())
    const source = getLastCrawlSource()

    const callsBeforeProgress = getStock.mock.calls.length
    source.emit({ status: 'stock_judgment_progress', judged: 40, total: 120, id: 1 })
    await waitFor(() => expect(getStock.mock.calls.length).toBeGreaterThan(callsBeforeProgress))

    const callsBeforeComplete = getStock.mock.calls.length
    source.emit({ status: 'stock_judgment_complete', judged: 120, id: 1 })
    await waitFor(() => expect(getStock.mock.calls.length).toBeGreaterThan(callsBeforeComplete))
  })
})

describe('Source filter save chaining', () => {
  const GENRED_CATALOG_CRAWLER = { id: 9, site_name: 'Epitaph', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: null, genre: 'punk' }

  async function openSourceDropdown() {
    render(<App />)
    await waitFor(() => expect(screen.getByText('Store')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Store'))
    // Both the Store and Track panes render their own StockBrowser/SourceFilter
    // (only one is visible via a `hidden` class, both stay mounted), so there
    // are always two "Source" buttons in the DOM. The Store pane's div comes
    // first in App.tsx's JSX, so index 0 is always the Store one.
    const sourceButtons = await screen.findAllByRole('button', { name: 'Source' })
    fireEvent.click(sourceButtons[0])
    return screen.findByRole('checkbox', { name: 'Epitaph' })
  }

  it('does not fire the next save until the previous one settles, and resolves in issue order', async () => {
    getCrawlers.mockResolvedValue([GENRED_CATALOG_CRAWLER])
    let resolveFirst!: () => void
    postUserHiddenCrawlers.mockImplementationOnce(() => new Promise<void>((resolve) => { resolveFirst = resolve }))
    const checkbox = await openSourceDropdown()

    fireEvent.click(checkbox) // hides Epitaph -> [9]
    await waitFor(() => expect(postUserHiddenCrawlers).toHaveBeenCalledTimes(1))

    fireEvent.click(checkbox) // un-hides Epitaph -> []
    // second save must wait for the first (still-pending) save to settle
    expect(postUserHiddenCrawlers).toHaveBeenCalledTimes(1)

    resolveFirst()
    await waitFor(() => expect(postUserHiddenCrawlers).toHaveBeenCalledTimes(2))
    expect(postUserHiddenCrawlers).toHaveBeenNthCalledWith(1, [9])
    expect(postUserHiddenCrawlers).toHaveBeenNthCalledWith(2, [])
  })

  it('surfaces a failed save in the status bar without clobbering a subsequent successful save', async () => {
    getCrawlers.mockResolvedValue([GENRED_CATALOG_CRAWLER])
    postUserHiddenCrawlers.mockRejectedValueOnce(new Error('boom'))
    const checkbox = await openSourceDropdown()

    fireEvent.click(checkbox) // save 1: rejects
    fireEvent.click(checkbox) // save 2: resolves (chained after save 1)

    await waitFor(() => expect(postUserHiddenCrawlers).toHaveBeenCalledTimes(2))
    // save 2 succeeded and is the latest issued save, so its (non-)error must win
    expect(screen.queryByText('Could not save your source filter — try again.')).not.toBeInTheDocument()
  })

  it('surfaces the failure when the most recently issued save is the one that fails', async () => {
    getCrawlers.mockResolvedValue([GENRED_CATALOG_CRAWLER])
    postUserHiddenCrawlers.mockRejectedValue(new Error('boom'))
    const checkbox = await openSourceDropdown()

    fireEvent.click(checkbox)

    await waitFor(() => expect(screen.getByText('Could not save your source filter — try again.')).toBeInTheDocument())
  })
})

describe('Source filter initial load gating', () => {
  it('keeps the Source button disabled until the initial hidden set loads, then enables it', async () => {
    let resolveLoad!: (ids: number[]) => void
    getUserHiddenCrawlers.mockImplementationOnce(() => new Promise<number[]>((resolve) => { resolveLoad = resolve }))
    render(<App />)
    await waitFor(() => expect(screen.getByText('Store')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Store'))

    const sourceButtons = await screen.findAllByRole('button', { name: 'Source' })
    expect(sourceButtons[0]).toBeDisabled()

    resolveLoad([])
    await waitFor(() => expect(sourceButtons[0]).not.toBeDisabled())
  })

  it('leaves the Source button disabled and surfaces an error when the initial load fails', async () => {
    getUserHiddenCrawlers.mockRejectedValueOnce(new Error('boom'))
    render(<App />)
    await waitFor(() => expect(screen.getByText('Store')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Store'))

    await waitFor(() => expect(screen.getByText('Could not load your source filter — reload the page to try again.')).toBeInTheDocument())
    const sourceButtons = await screen.findAllByRole('button', { name: 'Source' })
    expect(sourceButtons[0]).toBeDisabled()
  })
})
