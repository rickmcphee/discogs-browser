import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import RecordBrowser from '../views/RecordBrowser'
import type { Release } from '../api/types'

const { matchedRelease, unmatchedRelease } = vi.hoisted(() => ({
  matchedRelease: {
    discogs_id: 'r1',
    artist: 'Miles Davis',
    title: 'Kind of Blue',
    year: 1959,
    label: 'Columbia',
    format: 'Vinyl',
    discogs_price: null,
    cover_image_url: '',
    discogs_url: 'https://discogs.com/release/1',
    plex_url: 'http://plex.local:32400/web/index.html#!/server/abc/details?key=/library/metadata/500',
    last_synced: '',
    listings: {},
  } as Release,
  unmatchedRelease: {
    discogs_id: 'r2',
    artist: 'Bill Evans',
    title: 'Waltz for Debby',
    year: 1961,
    label: 'Riverside',
    format: 'Vinyl',
    discogs_price: null,
    cover_image_url: '',
    discogs_url: 'https://discogs.com/release/2',
    plex_url: null,
    last_synced: '',
    listings: {},
  } as Release,
}))

vi.mock('../api/client', () => ({
  getReleases: vi.fn().mockResolvedValue({
    total: 2, page: 1, per_page: 50, releases: [matchedRelease, unmatchedRelease],
  }),
  getArtists: vi.fn().mockResolvedValue(['Miles Davis', 'Bill Evans']),
}))

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('localStorage', {
    getItem: () => null,
    setItem: () => {},
  })
})

describe('Plex match hyperlink — list view', () => {
  it('renders a matched title as a link to the Plex album', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} />)
    const link = await screen.findByRole('link', { name: 'Kind of Blue' })
    expect(link).toHaveAttribute('href', matchedRelease.plex_url as string)
  })

  it('renders an unmatched title as plain text, not a link', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} />)
    await screen.findByText('Waltz for Debby')
    expect(screen.queryByRole('link', { name: 'Waltz for Debby' })).not.toBeInTheDocument()
  })
})

describe('Plex match hyperlink — tile view', () => {
  it('links the tile title to Plex while cover/artist still link to Discogs', async () => {
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => (key.startsWith('collectionViewMode') ? 'tiles' : null),
      setItem: () => {},
    })
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} />)
    const titleLink = await screen.findByRole('link', { name: 'Kind of Blue' })
    expect(titleLink).toHaveAttribute('href', matchedRelease.plex_url as string)
    const artistLink = screen.getByRole('link', { name: /Miles Davis/ })
    expect(artistLink).toHaveAttribute('href', matchedRelease.discogs_url)
  })
})
