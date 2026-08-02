import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import RecordBrowser from '../views/RecordBrowser'
import type { Crawler } from '../api/types'

const getReleases = vi.fn()
const getArtists = vi.fn()

vi.mock('../api/client', () => ({
  getReleases: (...args: unknown[]) => getReleases(...args),
  getArtists: (...args: unknown[]) => getArtists(...args),
}))

const CRAWLERS: Crawler[] = [
  { id: 1, site_name: 'Amazon', module_path: '', crawler_type: 'release', enabled: true, last_run: null, base_url: null },
  { id: 2, site_name: 'Epitaph', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: null },
]

beforeEach(() => {
  getReleases.mockReset()
  getArtists.mockReset()
  getReleases.mockResolvedValue({ total: 0, page: 1, per_page: 250, releases: [] })
  getArtists.mockResolvedValue([])
  localStorage.clear()
})

describe('RecordBrowser', () => {
  it('renders a column for an enabled release-type crawler but not an enabled catalog-type crawler', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} crawlers={CRAWLERS} />)
    await waitFor(() => expect(screen.getByText('Amazon')).toBeTruthy())
    expect(screen.queryByText('Epitaph')).toBeNull()
  })

  it('does not render a column for a crawler in hiddenCrawlerIds even if enabled', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} crawlers={CRAWLERS} hiddenCrawlerIds={[1]} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(screen.queryByText('Amazon')).toBeNull()
  })

  it('does not render a sync button when onRefreshCollection is not provided', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(screen.queryByTitle('Sync collection from Discogs')).toBeNull()
  })

  it('calls onRefreshCollection when the sync button is clicked', async () => {
    const onRefreshCollection = vi.fn()
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} onRefreshCollection={onRefreshCollection} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    screen.getByTitle('Sync collection from Discogs').click()
    expect(onRefreshCollection).toHaveBeenCalledTimes(1)
  })

  it('disables the sync button while syncing', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} onRefreshCollection={() => {}} syncing />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(screen.getByTitle('Sync collection from Discogs')).toBeDisabled()
  })
})
