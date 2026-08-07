import type { ComponentProps } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import Settings from '../views/Settings'
import type { Crawler } from '../api/types'

const { getSettings, saveSettings, setCrawlerEnabled } = vi.hoisted(() => ({
  getSettings: vi.fn().mockResolvedValue({
    crawl_delay_seconds: 30,
    consecutive_failure_limit: 10,
    crawl_schedule: '',
    crawl_schedule_mode: 'missing',
    ebay_app_id: '',
    ebay_cert_id: '',
    stock_schedule: '',
  }),
  saveSettings: vi.fn().mockResolvedValue(undefined),
  setCrawlerEnabled: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('../api/client', () => ({
  getSettings,
  saveSettings,
  setCrawlerEnabled,
}))

const CRAWLERS: Crawler[] = [
  { id: 1, site_name: 'Amazon', module_path: '', crawler_type: 'release', enabled: true, last_run: null, base_url: null },
  { id: 2, site_name: 'Disabled Site', module_path: '', crawler_type: 'release', enabled: false, last_run: null, base_url: null },
  { id: 3, site_name: 'Epitaph', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: null },
]

const CATALOG_CRAWLERS_WITH_DISABLED: Crawler[] = [
  ...CRAWLERS,
  { id: 4, site_name: 'Disabled Catalog', module_path: '', crawler_type: 'catalog', enabled: false, last_run: null, base_url: null },
]

beforeEach(() => {
  vi.clearAllMocks()
})

function renderSettings(overrides: Partial<ComponentProps<typeof Settings>> = {}) {
  return render(
    <Settings
      crawlers={[]}
      onCrawlersChange={() => {}}
      onRefreshPrices={() => {}}
      onRefreshStock={() => {}}
      onRefreshRecommendations={() => {}}
      onClearRecommendations={() => {}}
      hasJudgedItems={false}
      isAdmin
      hiddenCrawlerIds={[]}
      onToggleCrawlerView={() => {}}
      stockSyncBusy={false}
      stockSyncCrawlerId={null}
      onRefreshStoreCrawler={() => {}}
      {...overrides}
    />
  )
}

describe('Settings', () => {
  it('renders no page heading and no Save button', async () => {
    renderSettings()
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(screen.queryByText('Settings')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
  })

  it('auto-saves a settings field after editing, with no Save button', async () => {
    renderSettings()
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const input = screen.getByLabelText('eBay Client ID')
    fireEvent.change(input, { target: { value: 'new-app-id' } })
    await waitFor(
      () => expect(saveSettings).toHaveBeenCalledWith(expect.objectContaining({ ebay_app_id: 'new-app-id' })),
      { timeout: 2000 }
    )
  })

  it('does not save immediately on edit — only after the debounce settles', async () => {
    renderSettings()
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    fireEvent.change(screen.getByLabelText('eBay Client ID'), { target: { value: 'new-app-id' } })
    expect(saveSettings).not.toHaveBeenCalled()
    await waitFor(() => expect(saveSettings).toHaveBeenCalled(), { timeout: 2000 })
  })

  it('does not auto-save on initial load when nothing was edited', async () => {
    renderSettings()
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 1200))
    expect(saveSettings).not.toHaveBeenCalled()
  })

  it('shows a clean error message (not raw JSON) when an auto-save fails', async () => {
    saveSettings.mockRejectedValueOnce(new Error(JSON.stringify({ detail: 'Invalid cron expression' })))
    renderSettings()
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    fireEvent.change(screen.getByLabelText('eBay Client ID'), { target: { value: 'new-app-id' } })
    await waitFor(
      () => expect(screen.getByText('Invalid cron expression')).toBeInTheDocument(),
      { timeout: 2000 }
    )
    expect(screen.queryByText(/"detail"/)).not.toBeInTheDocument()
  })

  it('does not render the removed screenshot-interval or shuffle rows', async () => {
    renderSettings()
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(screen.queryByText('Screenshot interval')).not.toBeInTheDocument()
    expect(screen.queryByText('Shuffle')).not.toBeInTheDocument()
  })

  it('shows both View and Crawl columns to an admin, for every crawler regardless of enabled state', async () => {
    renderSettings({ crawlers: CRAWLERS })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(screen.getByText('Amazon')).toBeInTheDocument()
    expect(screen.getByText('Disabled Site')).toBeInTheDocument()
    expect(screen.getAllByText('Visible').length).toBe(3)
    expect(screen.getAllByText('Enabled').length).toBe(2)
    expect(screen.getAllByText('Disabled').length).toBe(1)
  })

  it('marks a crawler in hiddenCrawlerIds as Hidden in the View column', async () => {
    renderSettings({ crawlers: CRAWLERS, hiddenCrawlerIds: [1] })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const amazonRow = screen.getByText('Amazon').closest('tr') as HTMLElement
    expect(amazonRow.textContent).toContain('Hidden')
  })

  it('calls onToggleCrawlerView when a View button is clicked', async () => {
    const onToggleCrawlerView = vi.fn()
    renderSettings({ crawlers: CRAWLERS, onToggleCrawlerView })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const amazonRow = screen.getByText('Amazon').closest('tr') as HTMLElement
    fireEvent.click(screen.getAllByText('Visible').find((el) => amazonRow.contains(el))!)
    expect(onToggleCrawlerView).toHaveBeenCalledWith(1)
  })

  it('hides admin-only controls and the Crawl column for a non-admin, and only lists enabled crawlers', async () => {
    renderSettings({ crawlers: CRAWLERS, isAdmin: false })
    expect(getSettings).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
    expect(screen.getByText('Amazon')).toBeInTheDocument()
    expect(screen.queryByText('Disabled Site')).not.toBeInTheDocument()
    expect(screen.queryByText('Enabled')).not.toBeInTheDocument()
    expect(screen.queryByText('Recommendations Management')).not.toBeInTheDocument()
    expect(screen.getByText('Collection & Wishlist Price Sources')).toBeInTheDocument()
    expect(screen.getByText('Store Catalog Sources')).toBeInTheDocument()
  })

  it('still shows View toggles to a non-admin', async () => {
    const onToggleCrawlerView = vi.fn()
    renderSettings({ crawlers: CRAWLERS, isAdmin: false, onToggleCrawlerView })
    const amazonRow = screen.getByText('Amazon').closest('tr') as HTMLElement
    fireEvent.click(screen.getAllByText('Visible').find((el) => amazonRow.contains(el))!)
    expect(onToggleCrawlerView).toHaveBeenCalledWith(1)
  })

  it('shows a per-row Refresh button only for catalog crawlers, and only to an admin', async () => {
    renderSettings({ crawlers: CRAWLERS })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const amazonRow = screen.getByText('Amazon').closest('tr') as HTMLElement // release crawler
    const epitaphRow = screen.getByText('Epitaph').closest('tr') as HTMLElement // catalog crawler
    expect(within(amazonRow).queryByTitle(/Refresh .* catalog now/)).not.toBeInTheDocument()
    expect(within(epitaphRow).getByTitle('Refresh Epitaph catalog now')).toBeInTheDocument()
  })

  it('does not show the per-row Refresh button to a non-admin', async () => {
    renderSettings({ crawlers: CRAWLERS, isAdmin: false })
    const epitaphRow = screen.getByText('Epitaph').closest('tr') as HTMLElement
    expect(within(epitaphRow).queryByTitle('Refresh Epitaph catalog now')).not.toBeInTheDocument()
  })

  it('disables the per-row Refresh button for a disabled catalog crawler', async () => {
    renderSettings({ crawlers: CATALOG_CRAWLERS_WITH_DISABLED })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const disabledRow = screen.getByText('Disabled Catalog').closest('tr') as HTMLElement
    expect(within(disabledRow).getByTitle('Refresh Disabled Catalog catalog now')).toBeDisabled()
  })

  it('disables every per-row Refresh button while a stock sync is running', async () => {
    renderSettings({ crawlers: CATALOG_CRAWLERS_WITH_DISABLED, stockSyncBusy: true })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(screen.getByTitle('Refresh Epitaph catalog now')).toBeDisabled()
  })

  it('calls onRefreshStoreCrawler with that crawler\'s id when its Refresh button is clicked', async () => {
    const onRefreshStoreCrawler = vi.fn()
    renderSettings({ crawlers: CRAWLERS, onRefreshStoreCrawler })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    fireEvent.click(screen.getByTitle('Refresh Epitaph catalog now'))
    expect(onRefreshStoreCrawler).toHaveBeenCalledWith(3)
  })

  it('disables the bulk Store Management Refresh button while a stock sync is running', async () => {
    renderSettings({ crawlers: CRAWLERS, stockSyncBusy: true })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const description = await screen.findByText('Scan all enabled catalog crawlers immediately.')
    const row = description.closest('tr') as HTMLElement
    expect(within(row).getByText('Refresh')).toBeDisabled()
  })

  it('buckets a catalog_browser crawler into the Store Catalog Sources table, not the release table', () => {
    const crawlers: Crawler[] = [
      ...CRAWLERS,
      { id: 4, site_name: 'Angry Young and Poor', module_path: '', crawler_type: 'catalog_browser', enabled: true, last_run: null, base_url: null },
    ]
    renderSettings({ crawlers, isAdmin: false })
    const tables = screen.getAllByRole('table')
    expect(within(tables[0]).queryByText('Angry Young and Poor')).not.toBeInTheDocument()
    expect(within(tables[1]).getByText('Angry Young and Poor')).toBeInTheDocument()
  })
})
