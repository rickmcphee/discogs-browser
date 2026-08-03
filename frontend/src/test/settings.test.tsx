import type { ComponentProps } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
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
      onExportRecommendations={() => {}}
      onClearRecommendations={() => {}}
      hasJudgedItems={false}
      isAdmin
      hiddenCrawlerIds={[]}
      onToggleCrawlerView={() => {}}
      {...overrides}
    />
  )
}

describe('Settings', () => {
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
})
