import type { ComponentProps } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, fireEvent, waitFor, within } from '@testing-library/react'
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
  setCrawlerEnabled: vi.fn().mockResolvedValue({ ok: true, discarded: 0 }),
}))

vi.mock('../api/client', () => ({
  getSettings,
  saveSettings,
  setCrawlerEnabled,
}))

const CRAWLERS: Crawler[] = [
  { id: 1, site_name: 'Amazon', module_path: '', crawler_type: 'release', enabled: true, last_run: null, base_url: null, genre_summary: null, genre: 'marketplace' },
  { id: 2, site_name: 'Disabled Site', module_path: '', crawler_type: 'release', enabled: false, last_run: null, base_url: null, genre_summary: null, genre: 'marketplace' },
  { id: 3, site_name: 'Epitaph', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: 'https://www.epitaph.com', genre_summary: 'Punk rock label.', genre: 'punk' },
]

const CATALOG_CRAWLERS_WITH_DISABLED: Crawler[] = [
  ...CRAWLERS,
  { id: 4, site_name: 'Disabled Catalog', module_path: '', crawler_type: 'catalog', enabled: false, last_run: null, base_url: null, genre_summary: null, genre: 'marketplace' },
]

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.useRealTimers()
})

// The auto-save debounce is a real 800ms timer, so every test that waits it
// out calls vi.useFakeTimers() and advances the clock instead of sleeping:
// deterministic (a stalled CI worker fires a real timer late, which is what
// made these flake) and instant. waitFor is unusable once the clock is faked
// — its polling runs on that clock — so waits are act flushes instead.
const settle = () => act(async () => {})
const advanceBy = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })

function renderSettings(overrides: Partial<ComponentProps<typeof Settings>> = {}) {
  return render(
    <Settings
      crawlers={[]}
      onCrawlersChange={() => {}}
      onRefreshPrices={() => {}}
      onRefreshStock={() => {}}
      isAdmin
      stockSyncBusy={false}
      stockSyncCrawlerId={null}
      onRefreshStoreCrawler={() => {}}
      priceRefreshBusy={false}
      {...overrides}
    />
  )
}

async function bulkStockRow(): Promise<HTMLElement> {
  const description = await screen.findByText('Scan all enabled catalog crawlers immediately.')
  return description.closest('tr') as HTMLElement
}

describe('Settings', () => {
  it('renders no page heading and no Save button', async () => {
    renderSettings()
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(screen.queryByText('Settings')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
  })

  it('auto-saves a settings field after editing, with no Save button', async () => {
    vi.useFakeTimers()
    renderSettings()
    await settle()
    expect(getSettings).toHaveBeenCalled()
    const input = screen.getByLabelText('eBay Client ID')
    fireEvent.change(input, { target: { value: 'new-app-id' } })
    await advanceBy(800)
    expect(saveSettings).toHaveBeenCalledWith(expect.objectContaining({ ebay_app_id: 'new-app-id' }))
  })

  it('does not save immediately on edit — only after the debounce settles', async () => {
    vi.useFakeTimers()
    renderSettings()
    await settle()
    fireEvent.change(screen.getByLabelText('eBay Client ID'), { target: { value: 'new-app-id' } })
    expect(saveSettings).not.toHaveBeenCalled()
    await advanceBy(799)
    expect(saveSettings).not.toHaveBeenCalled()
    await advanceBy(1)
    expect(saveSettings).toHaveBeenCalled()
  })

  it('does not auto-save on initial load when nothing was edited', async () => {
    vi.useFakeTimers()
    renderSettings()
    await settle()
    expect(getSettings).toHaveBeenCalled()
    await advanceBy(1200)
    expect(saveSettings).not.toHaveBeenCalled()
  })

  it('shows a clean error message (not raw JSON) when an auto-save fails', async () => {
    vi.useFakeTimers()
    saveSettings.mockRejectedValueOnce(new Error(JSON.stringify({ detail: 'Invalid cron expression' })))
    renderSettings()
    await settle()
    fireEvent.change(screen.getByLabelText('eBay Client ID'), { target: { value: 'new-app-id' } })
    await advanceBy(800)
    expect(screen.getByText('Invalid cron expression')).toBeInTheDocument()
    expect(screen.queryByText(/"detail"/)).not.toBeInTheDocument()
  })

  it('does not render the removed screenshot-interval or shuffle rows', async () => {
    renderSettings()
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(screen.queryByText('Screenshot interval')).not.toBeInTheDocument()
    expect(screen.queryByText('Shuffle')).not.toBeInTheDocument()
  })

  it('does not render Recommendations Management, moved to the Account view', async () => {
    renderSettings()
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(screen.queryByText('Recommendations Management')).not.toBeInTheDocument()
  })

  it('shows the Crawl column to an admin, for every crawler regardless of enabled state', async () => {
    renderSettings({ crawlers: CRAWLERS })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(screen.getByText('Amazon')).toBeInTheDocument()
    expect(screen.getByText('Disabled Site')).toBeInTheDocument()
    expect(screen.queryByText('Visible')).not.toBeInTheDocument()
    expect(screen.getAllByText('Enabled').length).toBe(2)
    expect(screen.getAllByText('Disabled').length).toBe(1)
  })



  it('hides admin-only controls and the Crawl column for a non-admin, and only lists enabled crawlers', async () => {
    renderSettings({ crawlers: CRAWLERS, isAdmin: false })
    expect(getSettings).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
    expect(screen.getByText('Amazon')).toBeInTheDocument()
    expect(screen.queryByText('Disabled Site')).not.toBeInTheDocument()
    expect(screen.queryByText('Enabled')).not.toBeInTheDocument()
    expect(screen.getByText('Marketplaces')).toBeInTheDocument()
    expect(screen.getByText('Stores')).toBeInTheDocument()
  })

  it('shows the admin headings Marketplace Management and Store Management', async () => {
    renderSettings({ crawlers: CRAWLERS })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(screen.getByText('Marketplace Management')).toBeInTheDocument()
    expect(screen.getByText('Store Management')).toBeInTheDocument()
    expect(screen.queryByText('Crawler Management')).not.toBeInTheDocument()
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
    renderSettings({ crawlers: CRAWLERS, stockSyncBusy: true, stockSyncCrawlerId: 3 })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(within(await bulkStockRow()).getByText('Refresh')).toBeDisabled()
  })

  it('spins the bulk Store Management Refresh button while the bulk sync itself is running', async () => {
    renderSettings({ crawlers: CRAWLERS, stockSyncBusy: true, stockSyncCrawlerId: null })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const button = within(await bulkStockRow()).getByRole('button')
    expect(button).toHaveTextContent('Refreshing…')
    expect(button.querySelector('.animate-spin')).toBeInTheDocument()
  })

  // A single store's refresh holds the same lock, so the bulk button is
  // disabled -- but it is not what is running, and labelling it "Refreshing…"
  // would point at the wrong row.
  it('leaves the bulk Refresh button unspun while a single store is refreshing', async () => {
    renderSettings({ crawlers: CRAWLERS, stockSyncBusy: true, stockSyncCrawlerId: 3 })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const button = within(await bulkStockRow()).getByRole('button')
    expect(button).toHaveTextContent('Refresh')
    expect(button.querySelector('.animate-spin')).not.toBeInTheDocument()
  })

  it('spins the crawled store\'s own Refresh button and leaves it undimmed', async () => {
    renderSettings({ crawlers: CATALOG_CRAWLERS_WITH_DISABLED, stockSyncBusy: true, stockSyncCrawlerId: 3 })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const button = screen.getByTitle('Refreshing Epitaph catalog…')
    expect(button.querySelector('.animate-spin')).toBeInTheDocument()
    expect(button.className).not.toContain('disabled:opacity-30')
    expect(screen.queryByTitle('Refresh Epitaph catalog now')).not.toBeInTheDocument()
  })

  it('highlights the row of the store being crawled', async () => {
    renderSettings({ crawlers: CRAWLERS, stockSyncBusy: true, stockSyncCrawlerId: 3 })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const epitaphRow = screen.getByText('Epitaph').closest('tr') as HTMLElement
    const amazonRow = screen.getByText('Amazon').closest('tr') as HTMLElement
    expect(epitaphRow.className).toContain('bg-gray-800/60')
    expect(amazonRow.className).not.toContain('bg-gray-800/60')
  })

  it('spins the Marketplace Refresh button while its request is in flight', async () => {
    renderSettings({ crawlers: CRAWLERS, priceRefreshBusy: true })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const description = await screen.findByText('Run price crawlers immediately.')
    const button = within(description.closest('tr') as HTMLElement).getByRole('button')
    expect(button).toHaveTextContent('Starting…')
    expect(button).toBeDisabled()
    expect(button.querySelector('.animate-spin')).toBeInTheDocument()
  })

  it('buckets a catalog_browser crawler into the Stores table, not the Marketplaces table', () => {
    const crawlers: Crawler[] = [
      ...CRAWLERS,
      { id: 4, site_name: 'Angry Young and Poor', module_path: '', crawler_type: 'catalog_browser', enabled: true, last_run: null, base_url: null, genre_summary: null, genre: 'punk' },
    ]
    renderSettings({ crawlers, isAdmin: false })
    const tables = screen.getAllByRole('table')
    expect(within(tables[0]).queryByText('Angry Young and Poor')).not.toBeInTheDocument()
    expect(within(tables[1]).getByText('Angry Young and Poor')).toBeInTheDocument()
  })

  it('shows the discarded job count on the row that was just disabled', async () => {
    setCrawlerEnabled.mockResolvedValueOnce({ ok: true, discarded: 42 })
    renderSettings({ crawlers: CRAWLERS })
    await settle()

    const row = screen.getByText('Amazon').closest('tr') as HTMLElement
    fireEvent.click(within(row).getByText('Enabled'))
    await waitFor(() => expect(within(row).getByText('42 queued jobs discarded')).toBeInTheDocument())

    const otherRow = screen.getByText('Disabled Site').closest('tr') as HTMLElement
    expect(within(otherRow).queryByText(/queued jobs? discarded/)).not.toBeInTheDocument()
  })

  it('moves the notice when a second crawler is toggled', async () => {
    setCrawlerEnabled
      .mockResolvedValueOnce({ ok: true, discarded: 42 })
      .mockResolvedValueOnce({ ok: true, discarded: 7 })
    renderSettings({ crawlers: CRAWLERS })
    await settle()

    fireEvent.click(within(screen.getByText('Amazon').closest('tr') as HTMLElement).getByText('Enabled'))
    await waitFor(() => expect(screen.getByText('42 queued jobs discarded')).toBeInTheDocument())

    fireEvent.click(within(screen.getByText('Epitaph').closest('tr') as HTMLElement).getByText('Enabled'))
    await waitFor(() => expect(screen.getByText('7 queued jobs discarded')).toBeInTheDocument())
    expect(screen.queryByText('42 queued jobs discarded')).not.toBeInTheDocument()
  })

  it('surfaces a rejected toggle as an error and leaves the row showing its old state', async () => {
    setCrawlerEnabled.mockRejectedValueOnce(
      new Error(JSON.stringify({ detail: 'Admin access required' }))
    )
    const onCrawlersChange = vi.fn()
    renderSettings({ crawlers: CRAWLERS, onCrawlersChange })
    await settle()

    const row = screen.getByText('Amazon').closest('tr') as HTMLElement
    fireEvent.click(within(row).getByText('Enabled'))
    await waitFor(() => expect(screen.getByText('Admin access required')).toBeInTheDocument())

    expect(onCrawlersChange).not.toHaveBeenCalled()
    expect(within(row).getByText('Enabled')).toBeInTheDocument()
    expect(within(row).queryByText(/queued jobs? discarded/)).not.toBeInTheDocument()
  })

  // FastAPI's own 422s carry detail as an array of objects, not a string.
  // Returning that unchanged put a non-string into settingsSaveError, which
  // React throws on rendering — so the helper must fall back to the raw body.
  it('does not crash when a rejection carries a non-string detail', async () => {
    const body = JSON.stringify({ detail: [{ loc: ['body', 'enabled'], msg: 'field required' }] })
    setCrawlerEnabled.mockRejectedValueOnce(new Error(body))
    renderSettings({ crawlers: CRAWLERS })
    await settle()

    const row = screen.getByText('Amazon').closest('tr') as HTMLElement
    fireEvent.click(within(row).getByText('Enabled'))
    await waitFor(() => expect(screen.getByText(body)).toBeInTheDocument())
  })

  it('falls back to a default message when a rejection carries no message at all', async () => {
    setCrawlerEnabled.mockRejectedValueOnce(null)
    renderSettings({ crawlers: CRAWLERS })
    await settle()

    const row = screen.getByText('Amazon').closest('tr') as HTMLElement
    fireEvent.click(within(row).getByText('Enabled'))
    await waitFor(() => expect(screen.getByText('Could not change this crawler')).toBeInTheDocument())
  })

  it('shows no notice when nothing was discarded', async () => {
    setCrawlerEnabled.mockResolvedValueOnce({ ok: true, discarded: 0 })
    renderSettings({ crawlers: CRAWLERS })
    await settle()

    fireEvent.click(within(screen.getByText('Amazon').closest('tr') as HTMLElement).getByText('Enabled'))
    await settle()
    expect(screen.queryByText(/queued jobs? discarded/)).not.toBeInTheDocument()
  })

  it('shows the genre summary as a hover tooltip on the store link', async () => {
    renderSettings({ crawlers: CRAWLERS })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(screen.getByTitle('Punk rock label.')).toHaveTextContent('Epitaph')
  })

  it('shows no title attribute when a crawler has no genre summary', async () => {
    renderSettings({ crawlers: CRAWLERS })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(screen.getByText('Amazon')).not.toHaveAttribute('title')
  })

})
