import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import CollectionBrowser from '../views/CollectionBrowser'
import type { CrawlEvent, Release } from '../api/types'

const { release } = vi.hoisted(() => ({
  release: {
    discogs_id: 'r1',
    artist: 'Pink Floyd',
    title: 'The Wall',
    year: 1979,
    label: 'Harvest',
    format: 'Vinyl',
    discogs_price: null,
    cover_image_url: '',
    discogs_url: '',
    last_synced: '',
    listings: {
      Amazon: { url: 'https://amazon.com/x', price: 24.99, shipping: null, currency: 'USD', condition: null, last_checked: '' },
    },
  } as Release,
}))

vi.mock('../api/client', () => ({
  getReleases: vi.fn().mockResolvedValue({ total: 1, page: 1, per_page: 50, releases: [release] }),
  getArtists: vi.fn().mockResolvedValue(['Pink Floyd']),
}))

const crawler = { id: 1, site_name: 'Amazon', module_path: '', enabled: true, last_run: null, base_url: null, login_url: null }

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('localStorage', {
    getItem: () => null,
    setItem: () => {},
  })
})

describe('stale listing clearing on price refresh', () => {
  it('removes the listing on a not_found event during a manual single-item refresh', async () => {
    const notFound: CrawlEvent = { status: 'not_found', discogs_id: 'r1', site: 'Amazon' }
    const { rerender } = render(
      <CollectionBrowser
        onRefreshPrices={() => {}}
        crawling={true}
        crawlingReleaseId="r1"
        crawlEvents={[]}
        crawlers={[crawler]}
      />
    )
    await waitFor(() => expect(screen.getByText('$24.99')).toBeInTheDocument())

    rerender(
      <CollectionBrowser
        onRefreshPrices={() => {}}
        crawling={true}
        crawlingReleaseId="r1"
        crawlEvents={[notFound]}
        crawlers={[crawler]}
      />
    )

    await waitFor(() => expect(screen.queryByText('$24.99')).not.toBeInTheDocument())
    expect(screen.getByText('—', { selector: 'span' })).toBeInTheDocument()
  })

  it('also removes the listing on a not_found event during a bulk crawl (no crawlingReleaseId set)', async () => {
    const notFound: CrawlEvent = { status: 'not_found', discogs_id: 'r1', site: 'Amazon' }
    const { rerender } = render(
      <CollectionBrowser
        onRefreshPrices={() => {}}
        crawling={true}
        crawlingReleaseId={undefined}
        crawlEvents={[]}
        crawlers={[crawler]}
      />
    )
    await waitFor(() => expect(screen.getByText('$24.99')).toBeInTheDocument())

    rerender(
      <CollectionBrowser
        onRefreshPrices={() => {}}
        crawling={true}
        crawlingReleaseId={undefined}
        crawlEvents={[notFound]}
        crawlers={[crawler]}
      />
    )

    await waitFor(() => expect(screen.queryByText('$24.99')).not.toBeInTheDocument())
    expect(screen.getByText('—', { selector: 'span' })).toBeInTheDocument()
  })
})
