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
  { id: 1, site_name: 'Amazon', module_path: '', crawler_type: 'release', enabled: true, last_run: null, base_url: null, login_url: null },
  { id: 2, site_name: 'Epitaph', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: null, login_url: null },
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
})
