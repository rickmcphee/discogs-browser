import { useState, useEffect, useCallback, useRef, memo } from 'react'
import { getStock, getStockArtists } from '../api/client'
import type { StockItem, StockSortField, SortOrder, StockScope, LibraryScope } from '../api/types'
import { navButtonClass, dismissButtonClass } from '../styles/buttons'
import { textInputClass, selectClass } from '../styles/inputs'
import { reconcileSelectedArtist } from './artistSelection'

interface Props {
  scope?: StockScope
  recommendedAvailable?: boolean
  hiddenCrawlerIds?: number[]
  syncGeneration?: number
  isAdmin?: boolean
}

const NO_HIDDEN_CRAWLER_IDS: number[] = []
const STORE_FILTERS = ['all', 'recommended'] as const
const TRACK_FILTERS = ['all', 'collection', 'wantlist'] as const satisfies readonly LibraryScope[]

function trackLibraryScope(value: string): LibraryScope | undefined {
  return (TRACK_FILTERS as readonly string[]).includes(value) ? (value as LibraryScope) : undefined
}

function StockBrowser({ scope = 'store', recommendedAvailable = false, hiddenCrawlerIds = NO_HIDDEN_CRAWLER_IDS, syncGeneration, isAdmin = false }: Props) {
  const [items, setItems] = useState<StockItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [selectedArtist, setSelectedArtist] = useState('')
  const [artists, setArtists] = useState<string[]>([])
  const [sort, setSort] = useState<StockSortField>('artist')
  const [order, setOrder] = useState<SortOrder>('asc')
  const [filter, setFilter] = useState<string>(() => {
    const allowed: readonly string[] = scope === 'track' ? TRACK_FILTERS : STORE_FILTERS
    const stored = localStorage.getItem(`stockFilter_${scope}`)
    return stored && allowed.includes(stored) ? stored : 'all'
  })
  const [viewMode, setViewMode] = useState<'list' | 'tiles'>(
    () => (localStorage.getItem(`collectionViewMode_${scope}`) === 'tiles' ? 'tiles' : 'list')
  )
  const [hasLoaded, setHasLoaded] = useState(false)
  const PER_PAGE = 250
  const tableScrollRef = useRef<HTMLDivElement>(null)

  const [prevHiddenCrawlerIds, setPrevHiddenCrawlerIds] = useState(hiddenCrawlerIds)
  if (hiddenCrawlerIds !== prevHiddenCrawlerIds) {
    setPrevHiddenCrawlerIds(hiddenCrawlerIds)
    setPage(1)
  }

  // isLatest gates the commit rather than the request: reconciliation can clear
  // or re-case the selection while a request started under the old one is still
  // in flight, and that older filtered response arriving last would leave the
  // table showing a subset the sidebar no longer claims to be filtering by.
  const load = useCallback(async (isLatest: () => boolean = () => true) => {
    const result = await getStock({
      search: search || undefined,
      artist: selectedArtist || undefined,
      sort, order, page, per_page: PER_PAGE,
      libraryScope: scope === 'track' ? trackLibraryScope(filter) : undefined,
      recommended: scope === 'store' && filter === 'recommended',
      hiddenCrawlerIds,
    })
    if (!isLatest()) return
    setItems(result.items)
    setTotal(result.total)
    setHasLoaded(true)
  }, [search, selectedArtist, sort, order, page, filter, hiddenCrawlerIds, scope])

  // syncGeneration ticks on every stock_sync_progress/stock_sync_complete SSE
  // event so the store/track tabs repaint as crawlers add items, same as
  // RecordBrowser's syncGeneration does for collection sync. Kept in this
  // same effect as `load` (rather than a second `if (syncGeneration) load()`
  // effect) so a syncGeneration tick and an unrelated load-identity change
  // (search/sort/filter/page/...) can never both fire and double-call load().
  useEffect(() => {
    let latest = true
    load(() => latest)
    return () => { latest = false }
  }, [load, syncGeneration])
  useEffect(() => {
    if (!recommendedAvailable && filter === 'recommended') {
      setFilter('all')
    }
  }, [recommendedAvailable, filter])
  // Also refetches on syncGeneration ticks, same as load() above -- otherwise
  // the sidebar's artist list would go stale mid-crawl.
  useEffect(() => {
    // syncGeneration ticks faster than a request round-trip, so these overlap.
    // Committing whichever response lands last would let a stale list drive
    // the reconciliation below -- re-casing the selection to an old label, or
    // clearing an artist the newest response still lists.
    let latest = true
    getStockArtists(
      scope === 'track' ? trackLibraryScope(filter) : undefined,
      scope === 'store' && filter === 'recommended',
      hiddenCrawlerIds,
    ).then((list) => { if (latest) setArtists(list) })
    return () => { latest = false }
  }, [scope, filter, hiddenCrawlerIds, syncGeneration])
  // A refetched list can re-case the selected artist's label, or drop it
  // entirely -- see reconcileSelectedArtist. A pure re-casing keeps the current
  // sort and page (it's still the same artist); losing the artist delegates to
  // selectArtist(''), the full "back to All" transition, sort derivation
  // included.
  useEffect(() => {
    const next = reconcileSelectedArtist(artists, selectedArtist)
    if (next === selectedArtist) return
    if (next) setSelectedArtist(next)
    else selectArtist('')
  }, [artists, selectedArtist])
  useEffect(() => { localStorage.setItem(`collectionViewMode_${scope}`, viewMode) }, [viewMode, scope])
  useEffect(() => { localStorage.setItem(`stockFilter_${scope}`, filter) }, [filter, scope])
  useEffect(() => { tableScrollRef.current?.scrollTo({ top: 0 }) }, [selectedArtist])

  function changeFilter(value: string) {
    setFilter(value)
    setPage(1)
    // A narrower filter can drop the selected artist out of the sidebar
    // entirely, which would leave artist= still going out with nothing in the
    // sidebar highlighted -- an invisible filter the user has no way to
    // attribute. Clearing it is exactly the "back to all artists" transition
    // selectArtist('') already models, sort derivation included, so it
    // delegates rather than repeating it. That path also lands on 'artist',
    // so it subsumes the discogs_price reset below.
    if (selectedArtist) {
      selectArtist('')
      return
    }
    // The backend's discogs_price sort key is pinned to collection scope -- a
    // wantlist row has no paid price -- so under Wantlist it silently degrades
    // to artist order. Resetting the sort keeps the visible sort indicator
    // honest instead of leaving state claiming an order the rows aren't in.
    if (value === 'wantlist' && sort === 'discogs_price') {
      setSort('artist')
      setOrder('asc')
    }
  }

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
  const priceSortable = scope === 'track' && filter !== 'wantlist'
  const emptyMessage =
    scope === 'store' && filter === 'recommended' ? 'Nothing recommended is in stock right now.'
    : scope === 'store' ? (isAdmin ? 'No in-stock items yet. Click Refresh under Store Management in Settings.' : 'No in-stock items yet. Check back after the next store sync.')
    : filter === 'collection' ? 'Nothing in your collection is in stock right now.'
    : filter === 'wantlist' ? 'Nothing on your wantlist is in stock right now.'
    : "Nothing you're tracking is in stock right now."

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
              className={`w-full px-3 py-1.5 pr-8 text-sm ${textInputClass()}`}
            />
            <button
              onClick={() => { setSearch(''); setPage(1) }}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
            >
              ✕
            </button>
          </div>
          <span className="ml-3 text-xs text-gray-500">{total} items</span>
          <div className="ml-auto flex items-center gap-2">
            <select
              value={filter}
              onChange={(e) => changeFilter(e.target.value)}
              className={`px-3 py-1 text-sm ${selectClass()}`}
            >
              {scope === 'track' ? (
                <>
                  <option value="all">All</option>
                  <option value="collection">Collection</option>
                  <option value="wantlist">Wantlist</option>
                </>
              ) : (
                <>
                  <option value="all">All</option>
                  <option value="recommended" disabled={!recommendedAvailable}>Recommended</option>
                </>
              )}
            </select>
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
            {hasLoaded && items.length === 0 && (
              <div className="text-center py-8 text-gray-500">
                {emptyMessage}
              </div>
            )}
            {items.length > 0 && (
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
                  priceSortable ? (
                    <th className="text-center" aria-sort={sort === 'discogs_price' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}>
                      <button type="button" onClick={() => toggleSort('discogs_price')} className={`${sortButtonClass} text-center`}>
                        Price {sort === 'discogs_price' ? (order === 'asc' ? '↑' : '↓') : ''}
                      </button>
                    </th>
                  ) : (
                    <th className="text-center px-3 py-2">Price</th>
                  )
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
              {hasLoaded && items.length === 0 && (
                <tr><td colSpan={colCount} className="text-center py-8 text-gray-500">{emptyMessage}</td></tr>
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
