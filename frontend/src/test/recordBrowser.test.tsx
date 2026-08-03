import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
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

  it('links the cover icon to Discogs and leaves the artist name as plain text, in tile view', async () => {
    getReleases.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      releases: [{
        discogs_id: 'r1', artist: 'Pink Floyd', title: 'The Wall', year: 1979, label: 'Harvest',
        format: 'Vinyl', discogs_price: null, cover_image_url: 'https://x/cover.jpg',
        discogs_url: 'https://discogs.com/r1', plex_url: null, plex_matched_at: null,
        last_synced: '', listings: {},
      }],
    })
    localStorage.setItem('collectionViewMode_collection', 'tiles')
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} />)
    const icon = await screen.findByAltText('The Wall')
    expect(icon.closest('a')?.getAttribute('href')).toBe('https://discogs.com/r1')
    const artistText = screen.getByText('Pink Floyd')
    expect(artistText.closest('a')).toBeNull()
  })

  it('links the cover icon to Discogs and leaves the artist name as plain text, in list view', async () => {
    getReleases.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      releases: [{
        discogs_id: 'r1', artist: 'Pink Floyd', title: 'The Wall', year: 1979, label: 'Harvest',
        format: 'Vinyl', discogs_price: null, cover_image_url: 'https://x/cover.jpg',
        discogs_url: 'https://discogs.com/r1', plex_url: null, plex_matched_at: null,
        last_synced: '', listings: {},
      }],
    })
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} />)
    const icon = await screen.findByAltText('The Wall')
    expect(icon.closest('a')?.getAttribute('href')).toBe('https://discogs.com/r1')
    const artistText = screen.getByText('Pink Floyd')
    expect(artistText.closest('a')).toBeNull()
  })

  it('shows the Unmatched filter dropdown for the collection scope but not wishlist', async () => {
    const { rerender } = render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(screen.getByRole('combobox')).toBeInTheDocument()
    rerender(<RecordBrowser scope="wishlist" onRefreshPrices={() => {}} />)
    await waitFor(() => expect(screen.queryByRole('combobox')).not.toBeInTheDocument())
  })

  it('passes unmatched to getReleases when the filter is set to Unmatched', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'unmatched' } })
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ unmatched: true })))
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
