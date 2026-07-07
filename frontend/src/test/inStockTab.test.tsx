import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import App from '../App'

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
const getSettings = vi.fn()
const getJudgmentStatus = vi.fn()

vi.mock('../api/client', () => ({
  checkHealth: vi.fn().mockResolvedValue(true),
  getAuthState: vi.fn().mockResolvedValue('authenticated'),
  setUnauthorizedHandler: vi.fn(),
  refreshCollection: vi.fn().mockResolvedValue({ synced: 0, username: 'test' }),
  getCollectionStatus: vi.fn().mockResolvedValue({ total: 0, last_synced: null }),
  getCrawlStatus: vi.fn().mockResolvedValue({ total: 0, missing: 0, oldest_checked: null }),
  postCrawlStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  getCrawlers: vi.fn().mockResolvedValue([]),
  openCrawlStream: vi.fn(() => new MockEventSource()),
  getReleases: vi.fn().mockResolvedValue({ total: 0, page: 1, per_page: 50, releases: [] }),
  getArtists: vi.fn().mockResolvedValue([]),
  getSettings: (...args: unknown[]) => getSettings(...args),
  saveSettings: vi.fn(),
  setCrawlerEnabled: vi.fn(),
  getAuthStatus: vi.fn().mockResolvedValue({ active: false, active_site: null, has_state: false, state_mtime: null }),
  startLogin: vi.fn(),
  finishLogin: vi.fn(),
  clearAuthState: vi.fn(),
  changePassword: vi.fn(),
  logout: vi.fn(),
  openLogsStream: vi.fn(() => new MockEventSource()),
  screenshotUrl: vi.fn((path: string) => `/api/screenshots/${path}`),
  clearLogs: vi.fn(),
  getStock: vi.fn().mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] }),
  getStockArtists: vi.fn().mockResolvedValue([]),
  postStockSyncStart: (...args: unknown[]) => postStockSyncStart(...args),
  postJudgmentStart: (...args: unknown[]) => postJudgmentStart(...args),
  getJudgmentStatus: (...args: unknown[]) => getJudgmentStatus(...args),
}))

function getLastCrawlSource() {
  return MockEventSource.instances[MockEventSource.instances.length - 1]
}

const defaultSettings = {
  discogs_token: '', debug_screenshot_interval: 20, shuffle_crawl_order: true,
  crawl_delay_seconds: 30, consecutive_failure_limit: 10, crawl_schedule: '',
  crawl_schedule_mode: 'missing', collection_schedule: '', collection_schedule_mode: 'all',
  ebay_app_id: '', ebay_cert_id: '', stock_schedule: '', anthropic_api_key: '',
  recommendation_item_limit: 300,
}

beforeEach(() => {
  MockEventSource.instances = []
  vi.clearAllMocks()
  postStockSyncStart.mockResolvedValue({ started: true, running: true })
  postJudgmentStart.mockResolvedValue({ started: true, running: true })
  getSettings.mockResolvedValue(defaultSettings)
  getJudgmentStatus.mockResolvedValue({ any_judged: false })
})

describe('In Stock tab', () => {
  it('shows a Store nav button that switches views', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getByText('Store')).toBeInTheDocument())
    const storeButton = screen.getByText('Store')
    fireEvent.click(storeButton)
    await waitFor(() => expect(storeButton.className).toContain('bg-indigo-600'))
  })

  it('calls postStockSyncStart when Refresh Now is clicked in Settings', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    const description = await screen.findByText('Scan all enabled catalog crawlers immediately.')
    const row = description.closest('tr') as HTMLElement
    fireEvent.click(within(row).getByText('Refresh Now'))
    await waitFor(() => expect(postStockSyncStart).toHaveBeenCalled())
  })

  it('surfaces stock_sync_progress events in the bottom status bar', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_sync_progress', synced: 3, source: 'Nuclear Blast' })
    await waitFor(() => expect(screen.getByText(/Syncing in-stock catalog… 3 items \(Nuclear Blast\)/)).toBeInTheDocument())
  })

  it('surfaces stock_sync_complete events in the bottom status bar', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_sync_complete', synced: 12 })
    await waitFor(() => expect(screen.getByText(/In-stock sync complete: 12 items/)).toBeInTheDocument())
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
    source.emit({ status: 'stock_judgment_progress', judged: 5, total: 40 })
    await waitFor(() => expect(screen.getByText(/Finding recommendations for Store items… 5\/40/)).toBeInTheDocument())
  })

  it('surfaces stock_judgment_complete events in the bottom status bar', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_judgment_complete', judged: 12 })
    await waitFor(() => expect(screen.getByText(/Finished finding recommendations — 12 items checked/)).toBeInTheDocument())
  })

  it('surfaces stock_judgment_error events in the bottom status bar', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_judgment_error', error: 'boom' })
    await waitFor(() => expect(screen.getByText(/Finding recommendations failed: boom/)).toBeInTheDocument())
  })

  it('calls postJudgmentStart when Refresh Recommendations is clicked in Settings', async () => {
    getSettings.mockResolvedValue({ ...defaultSettings, anthropic_api_key: 'sk-ant-test' })
    render(<App />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    await waitFor(() => expect(screen.getByText('Refresh Recommendations')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Refresh Recommendations'))
    await waitFor(() => expect(postJudgmentStart).toHaveBeenCalled())
  })

  it('enables Recommended in Store only once a key is configured and a judgment has completed', async () => {
    getSettings.mockResolvedValue({ ...defaultSettings, anthropic_api_key: 'sk-ant-test' })
    getJudgmentStatus.mockResolvedValue({ any_judged: true })
    render(<App />)
    await waitFor(() => expect(screen.getByText('Store')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Store'))
    await waitFor(() => {
      const option = screen.getByRole('option', { name: 'Recommended' }) as HTMLOptionElement
      expect(option.disabled).toBe(false)
    })
  })

  it('disables Recommended in Store again while a judgment run is in progress', async () => {
    getSettings.mockResolvedValue({ ...defaultSettings, anthropic_api_key: 'sk-ant-test' })
    getJudgmentStatus.mockResolvedValue({ any_judged: true })
    render(<App />)
    await waitFor(() => expect(screen.getByText('Store')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Store'))
    await waitFor(() => expect((screen.getByRole('option', { name: 'Recommended' }) as HTMLOptionElement).disabled).toBe(false))
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_judgment_started' })
    await waitFor(() => expect((screen.getByRole('option', { name: 'Recommended' }) as HTMLOptionElement).disabled).toBe(true))
  })
})
