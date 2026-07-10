import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import RecordBrowser from '../views/RecordBrowser'
import type { Release } from '../api/types'

const { getReleases, getArtists } = vi.hoisted(() => ({
  getReleases: vi.fn().mockResolvedValue({ total: 0, page: 1, per_page: 50, releases: [] as Release[] }),
  getArtists: vi.fn().mockResolvedValue([]),
}))

vi.mock('../api/client', () => ({
  getReleases,
  getArtists,
}))

beforeEach(() => {
  vi.clearAllMocks()
  getReleases.mockResolvedValue({ total: 0, page: 1, per_page: 50, releases: [] })
  getArtists.mockResolvedValue([])
  localStorage.clear()
})

describe('Collection "No Plex" filter', () => {
  it('renders the filter dropdown on the Collection tab, defaulting to All', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    const select = screen.getByRole('combobox') as HTMLSelectElement
    expect(select.value).toBe('all')
    expect(Array.from(select.options).map((o) => o.text)).toEqual(['All', 'No Plex'])
  })

  it('does not render the filter dropdown on the Wishlist tab', async () => {
    render(<RecordBrowser scope="wishlist" onRefreshPrices={() => {}} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(screen.queryByRole('combobox')).toBeNull()
  })

  it('disables No Plex when plexAvailable is not set', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect((screen.getByRole('option', { name: 'No Plex' }) as HTMLOptionElement).disabled).toBe(true)
  })

  it('enables No Plex when plexAvailable is true', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} plexAvailable />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect((screen.getByRole('option', { name: 'No Plex' }) as HTMLOptionElement).disabled).toBe(false)
  })

  it('filters to unmatched releases when No Plex is selected', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} plexAvailable />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'no_plex' } })
    await waitFor(() => expect(getReleases).toHaveBeenCalledWith(expect.objectContaining({ no_plex: true })))
  })

  it('turns the filter back off when All is selected after No Plex', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} plexAvailable />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'no_plex' } })
    await waitFor(() => expect(getReleases).toHaveBeenCalledWith(expect.objectContaining({ no_plex: true })))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'all' } })
    await waitFor(() => expect(getReleases).toHaveBeenCalledWith(expect.objectContaining({ no_plex: false })))
  })

  it('refetches the artist sidebar scoped to no_plex when No Plex is selected', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} plexAvailable />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(getArtists).toHaveBeenLastCalledWith('collection', false)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'no_plex' } })
    await waitFor(() => expect(getArtists).toHaveBeenLastCalledWith('collection', true))
  })

  it('resets to All when plexAvailable becomes false while No Plex is selected', async () => {
    localStorage.setItem('collectionFilter_collection', 'no_plex')
    const { rerender } = render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} plexAvailable />)
    await waitFor(() => expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('no_plex'))
    rerender(<RecordBrowser scope="collection" onRefreshPrices={() => {}} plexAvailable={false} />)
    await waitFor(() => expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('all'))
  })

  it('persists the filter to localStorage under collectionFilter_collection and restores it on remount', async () => {
    const { unmount } = render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} plexAvailable />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'no_plex' } })
    await waitFor(() => expect(localStorage.getItem('collectionFilter_collection')).toBe('no_plex'))
    unmount()
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} plexAvailable />)
    await waitFor(() => expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('no_plex'))
  })

  it('scopes the persisted filter per tab so Wishlist is unaffected by Collection\'s selection', async () => {
    localStorage.setItem('collectionFilter_collection', 'no_plex')
    render(<RecordBrowser scope="wishlist" onRefreshPrices={() => {}} />)
    await waitFor(() => expect(getReleases).toHaveBeenCalled())
    expect(getReleases).toHaveBeenCalledWith(expect.objectContaining({ no_plex: false }))
  })
})
