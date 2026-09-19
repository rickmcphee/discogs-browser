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
    localStorage.setItem('collectionViewMode_collection', 'tiles')
    render(<RecordBrowser scope="collection" />)
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
    render(<RecordBrowser scope="collection" />)
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
    localStorage.setItem('collectionViewMode_collection', 'tiles')
    render(<RecordBrowser scope="collection" />)
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
    render(<RecordBrowser scope="collection" />)
    const link = await screen.findByRole('link', { name: 'View Pink Floyd – The Wall on Discogs' })
    expect(link).toHaveAttribute('href', 'https://discogs.com/r1')
  })

  it('shows the Unmatched filter dropdown for the collection scope but not wantlist', async () => {
    const { rerender } = render(<RecordBrowser scope="collection" />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(screen.getByRole('combobox')).toBeInTheDocument()
    rerender(<RecordBrowser scope="wantlist" />)
    await waitFor(() => expect(screen.queryByRole('combobox')).not.toBeInTheDocument())
  })

  it('passes unmatched to getReleases when the filter is set to Unmatched', async () => {
    render(<RecordBrowser scope="collection" />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'unmatched' } })
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ unmatched: true })))
  })

  it('does not render a sync button when onRefreshCollection is not provided', async () => {
    render(<RecordBrowser scope="collection" />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(screen.queryByTitle('Sync collection from Discogs')).toBeNull()
  })

  it('calls onRefreshCollection when the sync button is clicked', async () => {
    const onRefreshCollection = vi.fn()
    render(<RecordBrowser scope="collection" onRefreshCollection={onRefreshCollection} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    screen.getByTitle('Sync collection from Discogs').click()
    expect(onRefreshCollection).toHaveBeenCalledTimes(1)
  })

  it('disables the sync button while syncing', async () => {
    render(<RecordBrowser scope="collection" onRefreshCollection={() => {}} syncing />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(screen.getByTitle('Sync collection from Discogs')).toBeDisabled()
  })

  it('labels the wantlist sync button distinctly from the collection one', async () => {
    const onRefreshCollection = vi.fn()
    render(<RecordBrowser scope="wantlist" onRefreshCollection={onRefreshCollection} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    screen.getByTitle('Sync wantlist from Discogs').click()
    expect(onRefreshCollection).toHaveBeenCalledTimes(1)
  })

  it('shows a wantlist-specific empty state', async () => {
    render(<RecordBrowser scope="wantlist" />)
    expect(await screen.findByText('No wantlist items yet. Add records to your wantlist on Discogs, then sync.')).toBeInTheDocument()
  })

  it('does not show the empty state while the initial fetch is still pending', async () => {
    let resolveFetch: (v: any) => void = () => {}
    getReleases.mockReturnValue(new Promise((resolve) => { resolveFetch = resolve }))
    render(<RecordBrowser scope="collection" />)
    expect(screen.queryByText('No records found. Click the sync icon above to load your collection from Discogs.')).toBeNull()
    resolveFetch({ total: 0, page: 1, per_page: 250, releases: [] })
    await screen.findByText('No records found. Click the sync icon above to load your collection from Discogs.')
  })

  it('defaults sort to title when a specific artist is selected, and back to artist for All', async () => {
    getArtists.mockResolvedValue(['Pink Floyd'])
    render(<RecordBrowser scope="collection" />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Pink Floyd' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Pink Floyd' }))
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'title', order: 'asc' })))
    fireEvent.click(screen.getByRole('button', { name: 'All' }))
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'artist', order: 'asc' })))
  })

  it('refetches the artist nav list on every syncGeneration tick, not just on scope change', async () => {
    const { rerender } = render(<RecordBrowser scope="collection" syncGeneration={0} />)
    await waitFor(() => expect(getArtists).toHaveBeenCalledTimes(1))
    rerender(<RecordBrowser scope="collection" syncGeneration={1} />)
    await waitFor(() => expect(getArtists).toHaveBeenCalledTimes(2))
    rerender(<RecordBrowser scope="collection" syncGeneration={2} />)
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
    render(<RecordBrowser scope="collection" />)
    expect(await screen.findByText(new Date('2024-03-15T10:00:00Z').toLocaleDateString())).toBeInTheDocument()
    const row = (await screen.findByText('Radiohead')).closest('tr')!
    expect(within(row).getByText('—')).toBeInTheDocument()
  })

  it('sorts by Date Added when its header is clicked', async () => {
    render(<RecordBrowser scope="collection" />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    fireEvent.click(screen.getByText(/Date Added/))
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'date_added', order: 'asc' })))
  })

  it('renders the Price column by default', async () => {
    render(<RecordBrowser scope="collection" />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(screen.getByText(/Price/)).toBeTruthy()
  })

  it('hides the Price column when hasPriceField is false, and widens the empty-state row to match', async () => {
    render(<RecordBrowser scope="collection" hasPriceField={false} />)
    const emptyRow = await screen.findByText('No records found. Click the sync icon above to load your collection from Discogs.')
    expect(screen.queryByText(/Price/)).toBeNull()
    expect(emptyRow.closest('td')).toHaveAttribute('colSpan', '7')
  })

  it('resets a discogs_price sort to artist when hasPriceField flips to false', async () => {
    const { rerender } = render(<RecordBrowser scope="collection" hasPriceField={true} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    fireEvent.click(screen.getByText(/Price/))
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'discogs_price' })))
    rerender(<RecordBrowser scope="collection" hasPriceField={false} />)
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'artist', order: 'asc' })))
  })
})

describe('RecordBrowser persisted selections', () => {
  it('restores a stored artist filter into the first request it makes', async () => {
    getArtists.mockResolvedValue(['Pink Floyd'])
    localStorage.setItem('artistFilter_collection', 'Pink Floyd')
    render(<RecordBrowser scope="collection" />)
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ artist: 'Pink Floyd' })))
  })

  it('clears a stored artist the collection no longer holds, and takes the All transition with it', async () => {
    // reconcileSelectedArtist is what validates a restored label, since the
    // list that could validate it has not arrived at mount.
    getArtists.mockResolvedValue(['Radiohead'])
    localStorage.setItem('artistFilter_collection', 'Pink Floyd')
    localStorage.setItem('sortField_collection', 'title')
    render(<RecordBrowser scope="collection" />)
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ artist: undefined, sort: 'artist' })))
    expect(localStorage.getItem('artistFilter_collection')).toBe('')
  })

  it('keeps a restored artist visible in the sidebar when the artist list never arrives', async () => {
    // A rejected getArtists leaves the list unloaded for the session -- nothing
    // retries until the next sync tick -- while the restored artist goes on
    // filtering the rows. An empty sidebar there is a filter with nothing on
    // screen claiming it, which is the failure the whole reconciliation dance
    // exists to avoid.
    getArtists.mockRejectedValue(new Error('offline'))
    localStorage.setItem('artistFilter_collection', 'Pink Floyd')
    render(<RecordBrowser scope="collection" />)
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ artist: 'Pink Floyd' })))
    const button = screen.getByRole('button', { name: 'Pink Floyd' })
    expect(button.className).toContain('bg-white')
    expect(screen.getByRole('button', { name: 'All' }).className).not.toContain('bg-white')
  })

  it('offers All alone when nothing is selected and the list never arrives', async () => {
    getArtists.mockRejectedValue(new Error('offline'))
    render(<RecordBrowser scope="collection" />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: 'All' }).className).toContain('bg-white')
    expect(screen.getAllByRole('button').filter((b) => b.className.includes('text-sm px-2 py-1'))).toHaveLength(1)
  })

  it('persists a sort chosen from a column header, field and direction both', async () => {
    render(<RecordBrowser scope="collection" />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    fireEvent.click(screen.getByText(/Year/))
    await waitFor(() => expect(localStorage.getItem('sortField_collection')).toBe('year'))
    fireEvent.click(screen.getByText(/Year/))
    await waitFor(() => expect(localStorage.getItem('sortOrder_collection')).toBe('desc'))
  })

  it('restores a stored sort into the first request it makes', async () => {
    localStorage.setItem('sortField_collection', 'date_added')
    localStorage.setItem('sortOrder_collection', 'desc')
    render(<RecordBrowser scope="collection" />)
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'date_added', order: 'desc' })))
  })

  it('falls back to Artist when the stored sort names a field this build no longer offers', async () => {
    localStorage.setItem('sortField_collection', 'catalogue_number')
    localStorage.setItem('sortOrder_collection', 'sideways')
    render(<RecordBrowser scope="collection" />)
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'artist', order: 'asc' })))
  })

  it('restores the Unmatched filter, dropdown and request together', async () => {
    localStorage.setItem('unmatchedOnly_collection', 'true')
    render(<RecordBrowser scope="collection" />)
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ unmatched: true })))
    expect(screen.getByRole('combobox')).toHaveValue('unmatched')
  })

  it('persists the Unmatched filter when it is chosen', async () => {
    render(<RecordBrowser scope="collection" />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'unmatched' } })
    await waitFor(() => expect(localStorage.getItem('unmatchedOnly_collection')).toBe('true'))
  })

  it('keeps each scope\'s selections to itself', async () => {
    localStorage.setItem('sortField_collection', 'year')
    localStorage.setItem('collectionViewMode_collection', 'tiles')
    render(<RecordBrowser scope="wantlist" />)
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'artist' })))
    expect(localStorage.getItem('collectionViewMode_wantlist')).toBe('list')
  })

  it('keeps a restored Price sort while App has not yet answered whether there are prices', async () => {
    // hasPriceField is null until getPriceStatus() lands. Treating that as
    // "no prices" would reset every restored Price sort on first render.
    localStorage.setItem('sortField_collection', 'discogs_price')
    const { rerender } = render(<RecordBrowser scope="collection" hasPriceField={null} />)
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'discogs_price' })))
    rerender(<RecordBrowser scope="collection" hasPriceField={true} />)
    await waitFor(() => expect(screen.getByText(/Price/)).toBeTruthy())
    expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'discogs_price' }))
  })

  it('still drops a restored Price sort once there are definitely no prices', async () => {
    localStorage.setItem('sortField_collection', 'discogs_price')
    render(<RecordBrowser scope="collection" hasPriceField={false} />)
    await waitFor(() => expect(getReleases).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'artist', order: 'asc' })))
  })
})
