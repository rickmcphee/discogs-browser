import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import RecordBrowser from '../views/RecordBrowser'

const getReleases = vi.fn()
const getArtists = vi.fn()

vi.mock('../api/client', () => ({
  getReleases: (...args: unknown[]) => getReleases(...args),
  getArtists: (...args: unknown[]) => getArtists(...args),
}))

beforeEach(() => {
  getReleases.mockReset()
  getArtists.mockReset()
  getReleases.mockResolvedValue({ total: 0, page: 1, per_page: 250, releases: [] })
  getArtists.mockResolvedValue([])
  localStorage.clear()
})

describe('RecordBrowser', () => {
  it('links the cover icon to Discogs and leaves the artist name as plain text, in tile view', async () => {
    getReleases.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      releases: [{
        discogs_id: 'r1', artist: 'Pink Floyd', title: 'The Wall', year: 1979, label: 'Harvest',
        format: 'Vinyl', discogs_price: null, cover_image_url: 'https://x/cover.jpg',
        discogs_url: 'https://discogs.com/r1', plex_url: null, plex_matched_at: null,
        last_synced: '', date_added: null,
      }],
    })
    localStorage.setItem('collectionViewMode_discogs', 'tiles')
    render(<RecordBrowser scope="discogs" />)
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
        last_synced: '', date_added: null,
      }],
    })
    render(<RecordBrowser scope="discogs" />)
    const icon = await screen.findByAltText('The Wall')
    expect(icon.closest('a')?.getAttribute('href')).toBe('https://discogs.com/r1')
    const artistText = screen.getByText('Pink Floyd')
    expect(artistText.closest('a')).toBeNull()
  })

  it('gives the placeholder icon link an accessible name when there is no cover art, in tile view', async () => {
    getReleases.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      releases: [{
        discogs_id: 'r1', artist: 'Pink Floyd', title: 'The Wall', year: 1979, label: 'Harvest',
        format: 'Vinyl', discogs_price: null, cover_image_url: '',
        discogs_url: 'https://discogs.com/r1', plex_url: null, plex_matched_at: null,
        last_synced: '', date_added: null,
      }],
    })
    localStorage.setItem('collectionViewMode_discogs', 'tiles')
    render(<RecordBrowser scope="discogs" />)
    const link = await screen.findByRole('link', { name: 'View Pink Floyd – The Wall on Discogs' })
    expect(link).toHaveAttribute('href', 'https://discogs.com/r1')
  })

  it('gives the placeholder icon link an accessible name when there is no cover art, in list view', async () => {
    getReleases.mockResolvedValue({
      total: 1, page: 1, per_page: 250,
      releases: [{
        discogs_id: 'r1', artist: 'Pink Floyd', title: 'The Wall', year: 1979, label: 'Harvest',
        format: 'Vinyl', discogs_price: null, cover_image_url: '',
        discogs_url: 'https://discogs.com/r1', plex_url: null, plex_matched_at: null,
        last_synced: '', date_added: null,
      }],
    })
    render(<RecordBrowser scope="discogs" />)
    const link = await screen.findByRole('link', { name: 'View Pink Floyd – The Wall on Discogs' })
    expect(link).toHaveAttribute('href', 'https://discogs.com/r1')
  })

  it('shows the Unmatched filter dropdown for the discogs scope but not wishlist', async () => {
    const { rerender } = render(<RecordBrowser scope="discogs" />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(screen.getByRole('combobox')).toBeInTheDocument()
    rerender(<RecordBrowser scope="wishlist" />)
    await waitFor(() => expect(screen.queryByRole('combobox')).not.toBeInTheDocument())
  })

  it('passes unmatched to getReleases when the filter is set to Unmatched', async () => {
    render(<RecordBrowser scope="discogs" />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'unmatched' } })
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ unmatched: true })))
  })

  it('does not render a sync button when onRefreshCollection is not provided', async () => {
    render(<RecordBrowser scope="discogs" />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(screen.queryByTitle('Sync collection from Discogs')).toBeNull()
  })

  it('calls onRefreshCollection when the sync button is clicked', async () => {
    const onRefreshCollection = vi.fn()
    render(<RecordBrowser scope="discogs" onRefreshCollection={onRefreshCollection} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    screen.getByTitle('Sync collection from Discogs').click()
    expect(onRefreshCollection).toHaveBeenCalledTimes(1)
  })

  it('disables the sync button while syncing', async () => {
    render(<RecordBrowser scope="discogs" onRefreshCollection={() => {}} syncing />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(screen.getByTitle('Sync collection from Discogs')).toBeDisabled()
  })

  it('labels the wishlist sync button distinctly from the collection one', async () => {
    const onRefreshCollection = vi.fn()
    render(<RecordBrowser scope="wishlist" onRefreshCollection={onRefreshCollection} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    screen.getByTitle('Sync wishlist from Discogs').click()
    expect(onRefreshCollection).toHaveBeenCalledTimes(1)
  })

  it('defaults sort to title when a specific artist is selected, and back to artist for All', async () => {
    getArtists.mockResolvedValue(['Pink Floyd'])
    render(<RecordBrowser scope="discogs" />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Pink Floyd' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Pink Floyd' }))
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'title', order: 'asc' })))
    fireEvent.click(screen.getByRole('button', { name: 'All' }))
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'artist', order: 'asc' })))
  })

  it('refetches the artist nav list on every syncGeneration tick, not just on scope change', async () => {
    const { rerender } = render(<RecordBrowser scope="discogs" syncGeneration={0} />)
    await waitFor(() => expect(getArtists).toHaveBeenCalledTimes(1))
    rerender(<RecordBrowser scope="discogs" syncGeneration={1} />)
    await waitFor(() => expect(getArtists).toHaveBeenCalledTimes(2))
    rerender(<RecordBrowser scope="discogs" syncGeneration={2} />)
    await waitFor(() => expect(getArtists).toHaveBeenCalledTimes(3))
  })

  it('renders the Date Added column with a formatted date, or a dash when null', async () => {
    getReleases.mockResolvedValue({
      total: 2, page: 1, per_page: 250,
      releases: [
        {
          discogs_id: 'r1', artist: 'Pink Floyd', title: 'The Wall', year: 1979, label: 'Harvest',
          format: 'Vinyl', discogs_price: '$20', cover_image_url: '', discogs_url: '',
          plex_url: null, plex_matched_at: null, last_synced: '', date_added: '2024-03-15T10:00:00Z',
        },
        {
          discogs_id: 'r2', artist: 'Radiohead', title: 'Kid A', year: 2000, label: 'Parlophone',
          format: 'Vinyl', discogs_price: '$20', cover_image_url: '', discogs_url: '',
          plex_url: null, plex_matched_at: null, last_synced: '', date_added: null,
        },
      ],
    })
    render(<RecordBrowser scope="discogs" />)
    expect(await screen.findByText(new Date('2024-03-15T10:00:00Z').toLocaleDateString())).toBeInTheDocument()
    const row = (await screen.findByText('Radiohead')).closest('tr')!
    expect(within(row).getByText('—')).toBeInTheDocument()
  })

  it('sorts by Date Added when its header is clicked', async () => {
    render(<RecordBrowser scope="discogs" />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    fireEvent.click(screen.getByText(/Date Added/))
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'date_added', order: 'asc' })))
  })
})
