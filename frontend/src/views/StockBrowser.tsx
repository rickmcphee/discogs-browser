import { useState, useEffect, useCallback, useRef, memo } from 'react'
import { getStock, getStockArtists } from '../api/client'
import type { StockItem, StockSortField, SortOrder, StockScope } from '../api/types'
import { navButtonClass, dismissButtonClass } from '../styles/buttons'

interface Props {
  scope?: StockScope
  recommendedAvailable?: boolean
  hiddenCrawlerIds?: number[]
}

const NO_HIDDEN_CRAWLER_IDS: number[] = []

function StockBrowser({ scope = 'store', recommendedAvailable = false, hiddenCrawlerIds = NO_HIDDEN_CRAWLER_IDS }: Props) {
  const [items, setItems] = useState<StockItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [selectedArtist, setSelectedArtist] = useState('')
  const [artists, setArtists] = useState<string[]>([])
  const [sort, setSort] = useState<StockSortField>('artist')
  const [order, setOrder] = useState<SortOrder>('asc')
  const [filter, setFilter] = useState<'all' | 'recommended'>(() => {
    const stored = localStorage.getItem(`stockFilter_${scope}`)
    return stored === 'recommended' ? stored : 'all'
  })
  const [loading, setLoading] = useState(false)
  const [viewMode, setViewMode] = useState<'list' | 'tiles'>(
    () => (localStorage.getItem(`collectionViewMode_${scope}`) === 'tiles' ? 'tiles' : 'list')
  )
  const PER_PAGE = 250
  const tableScrollRef = useRef<HTMLDivElement>(null)

  const [prevHiddenCrawlerIds, setPrevHiddenCrawlerIds] = useState(hiddenCrawlerIds)
  if (hiddenCrawlerIds !== prevHiddenCrawlerIds) {
    setPrevHiddenCrawlerIds(hiddenCrawlerIds)
    setPage(1)
  }

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await getStock({
        search: search || undefined,
        artist: selectedArtist || undefined,
        sort, order, page, per_page: PER_PAGE,
        overlapping: scope === 'track',
        recommended: scope === 'store' && filter === 'recommended',
        hiddenCrawlerIds,
      })
      setItems(result.items)
      setTotal(result.total)
    } finally {
      setLoading(false)
    }
  }, [search, selectedArtist, sort, order, page, filter, hiddenCrawlerIds, scope])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    if (!recommendedAvailable && filter === 'recommended') {
      setFilter('all')
    }
  }, [recommendedAvailable, filter])
  useEffect(() => { getStockArtists(scope === 'track', scope === 'store' && filter === 'recommended', hiddenCrawlerIds).then(setArtists) }, [scope, filter, hiddenCrawlerIds])
  useEffect(() => { localStorage.setItem(`collectionViewMode_${scope}`, viewMode) }, [viewMode, scope])
  useEffect(() => { localStorage.setItem(`stockFilter_${scope}`, filter) }, [filter, scope])
  useEffect(() => { tableScrollRef.current?.scrollTo({ top: 0 }) }, [selectedArtist])

  function toggleSort(field: StockSortField) {
    if (sort === field) {
      setOrder((o) => (o === 'asc' ? 'desc' : 'asc'))
    } else {
      setSort(field)
      setOrder('asc')
    }
    setPage(1)
  }

  // Sorting by artist is meaningless once the list is filtered down to a
  // single artist, so switching the artist filter resets to the sort that
  // makes sense for the new context: artist for "All", title for a specific
  // artist. A later manual toggleSort still overrides this until the artist
  // filter changes again.
  function selectArtist(artist: string) {
    setSelectedArtist(artist)
    setSort(artist ? 'title' : 'artist')
    setOrder('asc')
    setPage(1)
  }

  const totalPages = Math.ceil(total / PER_PAGE)
  const colCount = scope === 'track' ? 7 : 6

  const sortButtonClass = 'w-full px-3 py-2 cursor-pointer hover:text-white select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-white/80'

  return (
    <div className="flex h-full overflow-hidden">
      {/* Sidebar */}
      <aside className="w-48 bg-gray-900 border-r border-gray-800 flex flex-col shrink-0 min-h-0">
        <div className="px-3 py-2 text-xs font-medium text-gray-500 uppercase tracking-wider border-b border-gray-800 shrink-0">Artist</div>
        <div className="flex flex-col gap-2 overflow-y-auto p-3">
          <button
            onClick={() => selectArtist('')}
            className={`shrink-0 text-left text-sm px-2 py-1 ${navButtonClass(!selectedArtist)}`}
          >
            All
          </button>
          {artists.map((a) => (
            <button
              key={a}
              onClick={() => selectArtist(a)}
              className={`shrink-0 text-left text-sm px-2 py-1 truncate ${navButtonClass(selectedArtist === a)}`}
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
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 pr-7 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-gray-400"
            />
            <button
              onClick={() => { setSearch(''); setPage(1) }}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
            >
              ✕
            </button>
          </div>
          <span className="ml-3 text-xs text-gray-500">{total} items</span>
          <div className="ml-auto flex items-center gap-2">
            {scope === 'store' && (
              <select
                value={filter}
                onChange={(e) => { setFilter(e.target.value as 'all' | 'recommended'); setPage(1) }}
                className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-gray-400"
              >
                <option value="all">All</option>
                <option value="recommended" disabled={!recommendedAvailable}>Recommended</option>
              </select>
            )}
            <button
              onClick={() => setViewMode('list')}
              title="List view"
              className={`p-1.5 ${navButtonClass(viewMode === 'list')}`}
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
              className={`p-1.5 ${navButtonClass(viewMode === 'tiles')}`}
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
            {loading && (
              <div className="flex items-center justify-center gap-2 py-8 text-gray-500">
                <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                Loading…
              </div>
            )}
            {!loading && items.length === 0 && (
              <div className="text-center py-8 text-gray-500">
                No in-stock items yet. Click "Refresh Stock Now" in Settings.
              </div>
            )}
            {!loading && items.length > 0 && (
              <div className="grid gap-4 p-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))' }}>
                {items.filter((item) => item.is_own).map((item) => (
                  <a
                    key={item.id}
                    href={item.url}
                    target="_blank"
                    rel="noreferrer"
                    className="group"
                  >
                    {item.cover_image_url ? (
                      <img
                        src={item.cover_image_url}
                        alt={item.title}
                        className="w-full aspect-square object-cover rounded"
                      />
                    ) : (
                      <div className="w-full aspect-square bg-gray-800 rounded" />
                    )}
                    <div className="mt-1.5 text-sm text-gray-200 truncate group-hover:text-white" title={item.reason ?? undefined}>{item.artist}</div>
                    <div className="text-xs text-gray-400 truncate" title={item.reason ?? undefined}>{item.title}</div>
                  </a>
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
                <th className="text-right" aria-sort={sort === 'artist' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}>
                  <button type="button" onClick={() => toggleSort('artist')} className={`${sortButtonClass} text-right`}>
                    Artist {sort === 'artist' ? (order === 'asc' ? '↑' : '↓') : ''}
                  </button>
                </th>
                <th className="text-left" aria-sort={sort === 'title' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}>
                  <button type="button" onClick={() => toggleSort('title')} className={`${sortButtonClass} text-left`}>
                    Title {sort === 'title' ? (order === 'asc' ? '↑' : '↓') : ''}
                  </button>
                </th>
                <th className="text-center" aria-sort={sort === 'format' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}>
                  <button type="button" onClick={() => toggleSort('format')} className={`${sortButtonClass} text-center`}>
                    Format {sort === 'format' ? (order === 'asc' ? '↑' : '↓') : ''}
                  </button>
                </th>
                {scope === 'track' && (
                  <th className="text-center" aria-sort={sort === 'discogs_price' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}>
                    <button type="button" onClick={() => toggleSort('discogs_price')} className={`${sortButtonClass} text-center`}>
                      Price {sort === 'discogs_price' ? (order === 'asc' ? '↑' : '↓') : ''}
                    </button>
                  </th>
                )}
                <th className="text-center" aria-sort={sort === 'price' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}>
                  <button type="button" onClick={() => toggleSort('price')} className={`${sortButtonClass} text-center`}>
                    Cost {sort === 'price' ? (order === 'asc' ? '↑' : '↓') : ''}
                  </button>
                </th>
                <th className="text-center" aria-sort={sort === 'source' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}>
                  <button type="button" onClick={() => toggleSort('source')} className={`${sortButtonClass} text-center`}>
                    Source {sort === 'source' ? (order === 'asc' ? '↑' : '↓') : ''}
                  </button>
                </th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={colCount} className="py-8 text-gray-500">
                  <div className="flex items-center justify-center gap-2">
                    <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    Loading…
                  </div>
                </td></tr>
              )}
              {!loading && items.length === 0 && (
                <tr><td colSpan={colCount} className="text-center py-8 text-gray-500">No in-stock items yet. Click "Refresh Stock Now" in Settings.</td></tr>
              )}
              {items.map((item) => (
                <tr key={item.id} className="border-t border-gray-800 hover:bg-gray-900/50">
                  <td className="px-3 py-2">
                    {item.cover_image_url ? (
                      <img
                        src={item.cover_image_url}
                        alt={item.title}
                        className="w-10 h-10 min-w-10 object-cover rounded"
                      />
                    ) : (
                      <div className="w-10 h-10 bg-gray-800 rounded" />
                    )}
                  </td>
                  <td className="px-3 py-2 text-right text-gray-200" title={item.reason ?? undefined}>{item.artist}</td>
                  <td className="px-3 py-2 text-left text-gray-300" title={item.reason ?? undefined}>{item.title}</td>
                  <td className="px-3 py-2 text-gray-400">{item.format ?? '—'}</td>
                  {scope === 'track' && (
                    <td className="px-3 py-2 text-gray-400">{item.discogs_price ?? '—'}</td>
                  )}
                  <td className="px-3 py-2">
                    <a href={item.url} target="_blank" rel="noreferrer" className="text-green-400 hover:text-green-300 font-medium">
                      {item.price != null ? `$${item.price.toFixed(2)}` : 'View'}
                    </a>
                  </td>
                  <td className="px-3 py-2 text-gray-400">{item.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="border-t border-gray-800 px-4 py-2 flex items-center gap-2 text-sm text-gray-400">
            <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1} className={`px-2 py-1 disabled:opacity-40 ${dismissButtonClass()}`}>← Prev</button>
            <span>Page {page} of {totalPages}</span>
            <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page === totalPages} className={`px-2 py-1 disabled:opacity-40 ${dismissButtonClass()}`}>Next →</button>
          </div>
        )}
      </div>
    </div>
  )
}

export default memo(StockBrowser)
