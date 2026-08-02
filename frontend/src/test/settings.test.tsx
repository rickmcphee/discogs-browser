import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import Settings from '../views/Settings'

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

beforeEach(() => {
  vi.clearAllMocks()
})

function renderSettings() {
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
})
