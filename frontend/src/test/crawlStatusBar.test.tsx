import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
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

vi.mock('../api/client', () => ({
  refreshCollection: vi.fn().mockResolvedValue({ synced: 0, username: 'test' }),
  getCollectionStatus: vi.fn().mockResolvedValue({ total: 0, last_synced: null }),
  getCrawlStatus: vi.fn().mockResolvedValue({ total: 0, missing: 0, oldest_checked: null }),
  getReleases: vi.fn().mockResolvedValue({ total: 0, page: 1, per_page: 50, releases: [] }),
  getArtists: vi.fn().mockResolvedValue([]),
  getCrawlers: vi.fn().mockResolvedValue([]),
  getSettings: vi.fn().mockResolvedValue({ discogs_token: '', anthropic_api_key: '' }),
  saveSettings: vi.fn(),
  setCrawlerEnabled: vi.fn(),
  openCrawlStream: vi.fn(() => {
    const src = new MockEventSource()
    return src
  }),
  openDiscoverStream: vi.fn(() => new MockEventSource()),
  openLogsStream: vi.fn(() => new MockEventSource()),
}))

function getLastCrawlSource() {
  return MockEventSource.instances[MockEventSource.instances.length - 1]
}

beforeEach(() => {
  MockEventSource.instances = []
  vi.clearAllMocks()
})

describe('crawl status bar', () => {
  it('shows "Refreshing prices…" after Refresh Prices is clicked', async () => {
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: /Refresh Prices/i }))
    await waitFor(() =>
      expect(screen.getByText(/Refreshing prices/i)).toBeInTheDocument()
    )
  })

  async function clickAndGetSource() {
    fireEvent.click(screen.getByRole('button', { name: /Refresh Prices/i }))
    // Wait for the async getCrawlStatus to resolve and openCrawlStream to be called
    await waitFor(() => expect(getLastCrawlSource()).toBeDefined())
    return getLastCrawlSource()
  }

  it('shows artist, title, and site from the current crawl event', async () => {
    render(<App />)
    const src = await clickAndGetSource()
    src.emit({ status: 'started', total: 2 })
    src.emit({ status: 'found', release: 'The Wall', artist: 'Pink Floyd', site: 'Amazon', price: 24.99 })

    await waitFor(() => expect(screen.getByText('Pink Floyd — The Wall')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText('Amazon')).toBeInTheDocument())
  })

  it('shows X/total progress count', async () => {
    render(<App />)
    const src = await clickAndGetSource()
    src.emit({ status: 'started', total: 4 })
    src.emit({ status: 'not_found', release: 'Wish You Were Here', artist: 'Pink Floyd', site: 'Amazon' })

    await waitFor(() => expect(screen.getByText(/1\/4/)).toBeInTheDocument())
  })

  it('shows Done and Dismiss when complete', async () => {
    render(<App />)
    const src = await clickAndGetSource()
    src.emit({ status: 'started', total: 1 })
    src.emit({ status: 'complete' })

    await waitFor(() => expect(screen.getByText('Done')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /Dismiss/i })).toBeInTheDocument()
  })

  it('hides the status bar after Dismiss', async () => {
    render(<App />)
    const src = await clickAndGetSource()
    src.emit({ status: 'complete' })

    await waitFor(() => screen.getByRole('button', { name: /Dismiss/i }))
    fireEvent.click(screen.getByRole('button', { name: /Dismiss/i }))

    expect(screen.queryByText('Done')).not.toBeInTheDocument()
  })
})
