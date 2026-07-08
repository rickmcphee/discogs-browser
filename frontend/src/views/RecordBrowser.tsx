import { useState, useEffect, useCallback, useRef } from 'react'
import { getReleases, getArtists } from '../api/client'
import type { Release, Crawler, SortField, SortOrder, CrawlEvent, RecordScope } from '../api/types'

interface Props {
  scope: RecordScope
  onRefreshPrices: (releaseId: string) => void
  crawling?: boolean
  crawlingReleaseId?: string
  crawlEvents?: CrawlEvent[]
  crawlers?: Crawler[]
}

export default function RecordBrowser({ scope, onRefreshPrices, crawling, crawlingReleaseId, crawlEvents, crawlers = [] }: Props) {
  const [releases, setReleases] = useState<Release[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [selectedArtist, setSelectedArtist] = useState('')
  const [artists, setArtists] = useState<string[]>([])
  const [sort, setSort] = useState<SortField>('artist')
  const [order, setOrder] = useState<SortOrder>('asc')
  const [loading, setLoading] = useState(false)
  const [viewMode, setViewMode] = useState<'list' | 'tiles'>(
    () => (localStorage.getItem(`collectionViewMode_${scope}`) === 'tiles' ? 'tiles' : 'list')
  )
  const PER_PAGE = 250

  const processedCount = useRef(0)
  const tableScrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!crawlEvents) return
    if (crawlEvents.length === 0) { processedCount.current = 0; return }
    const newEvents = crawlEvents.slice(processedCount.current)
    if (newEvents.length === 0) return
    processedCount.current = crawlEvents.length

    const foundEvents = newEvents.filter(e => e.status === 'found' && e.discogs_id && e.site)
    // The backend clears a release's stale listings before re-searching it (both
    // for bulk crawls and single-item refreshes), so "not found" means genuinely
    // not found — the already-loaded client state doesn't know that until we
    // clear it here too.
    const notFoundEvents = newEvents.filter(e => e.status === 'not_found' && e.discogs_id && e.site)
    if (foundEvents.length === 0 && notFoundEvents.length === 0) return

    setReleases(prev => prev.map(r => {
      const found = foundEvents.filter(e => e.discogs_id === r.discogs_id)
      const notFound = notFoundEvents.filter(e => e.discogs_id === r.discogs_id)
      if (found.length === 0 && notFound.length === 0) return r
      const updatedListings = { ...r.listings }
      for (const e of found) {
        updatedListings[e.site!] = {
          url: updatedListings[e.site!]?.url ?? '',
          price: e.price ?? null,
          shipping: updatedListings[e.site!]?.shipping ?? null,
          currency: updatedListings[e.site!]?.currency ?? null,
          condition: updatedListings[e.site!]?.condition ?? null,
          last_checked: updatedListings[e.site!]?.last_checked ?? new Date().toISOString(),
        }
      }
      for (const e of notFound) {
        delete updatedListings[e.site!]
      }
      return { ...r, listings: updatedListings }
    }))
  }, [crawlEvents])

  useEffect(() => {
    tableScrollRef.current?.scrollTo({ top: 0 })
  }, [selectedArtist])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await getReleases({
        search: search || undefined,
        artist: selectedArtist || undefined,
        sort,
        order,
        page,
        per_page: PER_PAGE,
        scope,
      })
      setReleases(result.releases)
      setTotal(result.total)
    } finally {
      setLoading(false)
    }
  }, [search, selectedArtist, sort, order, page, scope])

  useEffect(() => { load() }, [load])
  useEffect(() => { getArtists(scope).then(setArtists) }, [scope])
  useEffect(() => { localStorage.setItem(`collectionViewMode_${scope}`, viewMode) }, [viewMode, scope])

  function toggleSort(field: SortField) {
    if (sort === field) {
      setOrder((o) => (o === 'asc' ? 'desc' : 'asc'))
    } else {
      setSort(field)
      setOrder('asc')
    }
    setPage(1)
  }

  const enabledCrawlers = crawlers.filter((c) => c.enabled)
  const totalPages = Math.ceil(total / PER_PAGE)

  return (
    <div className="flex h-full overflow-hidden">
      {/* Sidebar */}
      <aside className="w-48 bg-gray-900 border-r border-gray-800 flex flex-col shrink-0 min-h-0">
        <div className="px-3 py-2 text-xs font-medium text-gray-500 uppercase tracking-wider border-b border-gray-800 shrink-0">Artist</div>
        <div className="flex flex-col gap-2 overflow-y-auto p-3">
          <button
            onClick={() => { setSelectedArtist(''); setPage(1) }}
            className={`shrink-0 text-left text-sm px-2 py-1 rounded ${!selectedArtist ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-white'}`}
          >
            All
          </button>
          {artists.map((a) => (
            <button
              key={a}
              onClick={() => { setSelectedArtist(a); setPage(1) }}
              className={`shrink-0 text-left text-sm px-2 py-1 rounded truncate ${selectedArtist === a ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-white'}`}
            >
              {a}
            </button>
          ))}
        </div>
      </aside>

      {/* Main */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Search bar */}
        <div className="px-4 py-3 border-b border-gray-800 bg-gray-950 flex items-center">
          <div className="relative w-full max-w-md">
            <input
              type="text"
              placeholder="Search artist or title…"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1) }}
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 pr-7 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-indigo-500"
            />
            <button
              onClick={() => { setSearch(''); setPage(1) }}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
            >
              ✕
            </button>
          </div>
          <span className="ml-3 text-xs text-gray-500">{total} records</span>
          <div className="ml-auto flex items-center gap-1">
            <button
              onClick={() => setViewMode('list')}
              title="List view"
              className={`p-1.5 rounded ${viewMode === 'list' ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-white'}`}
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <line x1="2" y1="4" x2="14" y2="4" />
                <line x1="2" y1="8" x2="14" y2="8" />
                <line x1="2" y1="12" x2="14" y2="12" />
              </svg>
            </button>
            <button
              onClick={() => setViewMode('tiles')}
              title="Tile view"
              className={`p-1.5 rounded ${viewMode === 'tiles' ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-white'}`}
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <rect x="2" y="2" width="5" height="5" />
                <rect x="9" y="2" width="5" height="5" />
                <rect x="2" y="9" width="5" height="5" />
                <rect x="9" y="9" width="5" height="5" />
              </svg>
            </button>
          </div>
        </div>

        {/* Tiles */}
        {viewMode === 'tiles' && (
          <div className="flex-1 overflow-auto" ref={tableScrollRef}>
            {loading && <div className="text-center py-8 text-gray-500">Loading…</div>}
            {!loading && releases.length === 0 && (
              <div className="text-center py-8 text-gray-500">
                {scope === 'wishlist'
                  ? 'No wishlist items yet. Add records to your wantlist on Discogs, then sync.'
                  : 'No records found. Click "Refresh Collection" to sync from Discogs.'}
              </div>
            )}
            {!loading && releases.length > 0 && (
              <div className="grid gap-4 p-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))' }}>
                {releases.map((r) => (
                  <div key={r.discogs_id} className="group">
                    <a href={r.discogs_url} target="_blank" rel="noreferrer">
                      {r.cover_image_url ? (
                        <img
                          src={r.cover_image_url}
                          alt={r.title}
                          className="w-full aspect-square object-cover rounded"
                        />
                      ) : (
                        <div className="w-full aspect-square bg-gray-800 rounded" />
                      )}
                      <div className="mt-1.5 text-sm text-gray-200 truncate group-hover:text-indigo-400">{r.artist}</div>
                    </a>
                    {r.plex_url ? (
                      <a
                        href={r.plex_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-xs text-gray-400 truncate hover:text-indigo-400 block"
                      >
                        {r.title}
                      </a>
                    ) : (
                      <div className="text-xs text-gray-400 truncate">{r.title}</div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Table */}
        {viewMode === 'list' && (
        <div className="flex-1 overflow-auto" ref={tableScrollRef}>
          <table className="w-full text-sm border-collapse">
            <thead className="sticky top-0 bg-gray-900 text-xs text-gray-400 uppercase">
              <tr>
                <th className="w-12 px-3 py-2"></th>
                <th
                  className="px-3 py-2 text-center cursor-pointer hover:text-white select-none"
                  onClick={() => toggleSort('artist')}
                >
                  Artist {sort === 'artist' ? (order === 'asc' ? '↑' : '↓') : ''}
                </th>
                <th
                  className="px-3 py-2 text-center cursor-pointer hover:text-white select-none"
                  onClick={() => toggleSort('title')}
                >
                  Title {sort === 'title' ? (order === 'asc' ? '↑' : '↓') : ''}
                </th>
                <th
                  className="px-3 py-2 text-center cursor-pointer hover:text-white select-none"
                  onClick={() => toggleSort('year')}
                >
                  Year {sort === 'year' ? (order === 'asc' ? '↑' : '↓') : ''}
                </th>
                <th
                  className="px-3 py-2 text-center cursor-pointer hover:text-white select-none"
                  onClick={() => toggleSort('label')}
                >
                  Label {sort === 'label' ? (order === 'asc' ? '↑' : '↓') : ''}
                </th>
                <th
                  className="px-3 py-2 text-center cursor-pointer hover:text-white select-none"
                  onClick={() => toggleSort('format')}
                >
                  Format {sort === 'format' ? (order === 'asc' ? '↑' : '↓') : ''}
                </th>
                <th
                  className="px-3 py-2 text-center cursor-pointer hover:text-white select-none"
                  onClick={() => toggleSort('discogs_price')}
                >
                  Price {sort === 'discogs_price' ? (order === 'asc' ? '↑' : '↓') : ''}
                </th>
                {enabledCrawlers.map((c) => (
                  <th
                    key={c.id}
                    className="px-3 py-2 text-center cursor-pointer hover:text-white select-none"
                    onClick={() => toggleSort(`price_${c.site_name}`)}
                  >
                    {c.site_name} {sort === `price_${c.site_name}` ? (order === 'asc' ? '↑' : '↓') : ''}
                  </th>
                ))}
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr>
                  <td colSpan={8 + enabledCrawlers.length} className="text-center py-8 text-gray-500">
                    Loading…
                  </td>
                </tr>
              )}
              {!loading && releases.length === 0 && (
                <tr>
                  <td colSpan={8 + enabledCrawlers.length} className="text-center py-8 text-gray-500">
                    {scope === 'wishlist'
                      ? 'No wishlist items yet. Add records to your wantlist on Discogs, then sync.'
                      : 'No records found. Click "Refresh Collection" to sync from Discogs.'}
                  </td>
                </tr>
              )}
              {releases.map((r) => (
                <tr key={r.discogs_id} className="border-t border-gray-800 hover:bg-gray-900/50">
                  <td className="px-3 py-2">
                    {r.cover_image_url ? (
                      <img
                        src={r.cover_image_url}
                        alt={r.title}
                        className="w-10 h-10 object-cover rounded"
                      />
                    ) : (
                      <div className="w-10 h-10 bg-gray-800 rounded" />
                    )}
                  </td>
                  <td className="px-3 py-2 text-gray-200">
                    <a
                      href={r.discogs_url}
                      target="_blank"
                      rel="noreferrer"
                      className="hover:text-indigo-400"
                    >
                      {r.artist}
                    </a>
                  </td>
                  <td className="px-3 py-2 text-gray-300">
                    {r.plex_url ? (
                      <a href={r.plex_url} target="_blank" rel="noreferrer" className="hover:text-indigo-400">
                        {r.title}
                      </a>
                    ) : (
                      r.title
                    )}
                  </td>
                  <td className="px-3 py-2 text-gray-400">{r.year ?? '—'}</td>
                  <td className="px-3 py-2 text-gray-400 truncate max-w-32">{r.label}</td>
                  <td className="px-3 py-2 text-gray-400">{r.format}</td>
                  <td className="px-3 py-2 text-gray-400">{r.discogs_price ?? '—'}</td>
                  {enabledCrawlers.map((c) => {
                    const listing = r.listings[c.site_name]
                    return (
                      <td key={c.id} className="px-3 py-2">
                        {listing ? (
                          <a
                            href={listing.url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-green-400 hover:text-green-300 font-medium"
                          >
                            {listing.price != null
                              ? `$${listing.price.toFixed(2)}`
                              : 'View'}
                          </a>
                        ) : (
                          <span className="text-gray-600">—</span>
                        )}
                      </td>
                    )
                  })}
                  <td className="px-3 py-2">
                    <button
                      onClick={() => onRefreshPrices(r.discogs_id)}
                      disabled={crawling}
                      className="text-xs text-gray-500 hover:text-indigo-400 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                      title="Refresh prices for this record"
                    >
                      {crawlingReleaseId === r.discogs_id ? '⟳' : '↻'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="border-t border-gray-800 px-4 py-2 flex items-center gap-2 text-sm text-gray-400">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="px-2 py-1 rounded hover:bg-gray-800 disabled:opacity-40"
            >
              ← Prev
            </button>
            <span>Page {page} of {totalPages}</span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className="px-2 py-1 rounded hover:bg-gray-800 disabled:opacity-40"
            >
              Next →
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
