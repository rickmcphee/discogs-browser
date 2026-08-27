import { useState, useEffect, useCallback, useRef } from 'react'
import { getReleases, getArtists } from '../api/client'
import type { Release, SortField, SortOrder, RecordScope } from '../api/types'
import { dismissButtonClass, navButtonClass } from '../styles/buttons'
import { textInputClass, selectClass } from '../styles/inputs'
import { reconcileSelectedArtist } from './artistSelection'
import { useIsMobile } from '../hooks/useMediaQuery'
import { ArtistSidebar, ArtistSheetButton } from '../components/ArtistFilter'
import MobileSort, { type SortOption } from '../components/MobileSort'

interface Props {
  scope: RecordScope
  syncing?: boolean
  onRefreshCollection?: () => void
  syncGeneration?: number
  hasPriceField?: boolean
}

// Rendered from the tile, card and table branches below, which would otherwise
// hold identical copies that drift apart.
const WANTLIST_EMPTY = 'No wantlist items yet. Add records to your wantlist on Discogs, then sync.'
const COLLECTION_EMPTY = 'No records found. Click the sync icon above to load your collection from Discogs.'

export default function RecordBrowser({ scope, syncing, onRefreshCollection, syncGeneration, hasPriceField = true }: Props) {
  const isMobile = useIsMobile()
  const [releases, setReleases] = useState<Release[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [selectedArtist, setSelectedArtist] = useState('')
  const [artists, setArtists] = useState<string[]>([])
  const [sort, setSort] = useState<SortField>('artist')
  const [order, setOrder] = useState<SortOrder>('asc')
  const [viewMode, setViewMode] = useState<'list' | 'tiles'>(
    () => (localStorage.getItem(`collectionViewMode_${scope}`) === 'tiles' ? 'tiles' : 'list')
  )
  const [unmatched, setUnmatched] = useState(false)
  const [hasLoaded, setHasLoaded] = useState(false)
  const PER_PAGE = 250

  const tableScrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    tableScrollRef.current?.scrollTo({ top: 0 })
  }, [selectedArtist])

  // isLatest gates the commit, not the request -- see StockBrowser's copy: a
  // request started under a selection the reconciliation then clears or re-cases
  // must not paint its filtered rows under a sidebar that has moved on.
  const load = useCallback(async (isLatest: () => boolean = () => true) => {
    const result = await getReleases({
      search: search || undefined,
      artist: selectedArtist || undefined,
      sort,
      order,
      page,
      per_page: PER_PAGE,
      scope,
      unmatched: scope === 'collection' ? unmatched : undefined,
    })
    if (!isLatest()) return
    setReleases(result.releases)
    setTotal(result.total)
    setHasLoaded(true)
  }, [search, selectedArtist, sort, order, page, scope, unmatched])

  // syncGeneration ticks on every sync_progress/sync_complete SSE event so the
  // collection/wantlist tables fill in as pages land, not just once the whole
  // sync finishes. One effect keyed on both, matching StockBrowser: as two
  // effects (a mount one on [load], a tick one on [syncGeneration, load]) a
  // load-identity change re-ran both and issued two requests for the same
  // query, and each kept its own `latest` flag, so a tick could not invalidate
  // an in-flight request from the other -- letting the older snapshot land last
  // and overwrite the fresher one mid-sync. The truthy guard the tick effect
  // needed to avoid a duplicate mount load goes away with it.
  useEffect(() => {
    let latest = true
    load(() => latest)
    return () => { latest = false }
  }, [load, syncGeneration])
  // Also refetches on syncGeneration ticks, same as load() above -- otherwise
  // the nav list stays stuck at whatever it was on mount while a collection
  // sync fills the table in page by page.
  // The `latest` guard is load-bearing, not hygiene: syncGeneration ticks per
  // sync_progress event, faster than a round-trip, so these requests overlap
  // and a late-arriving stale list would drive the reconciliation below.
  useEffect(() => {
    let latest = true
    getArtists(scope).then((list) => { if (latest) setArtists(list) })
    return () => { latest = false }
  }, [scope, syncGeneration])
  // A collection sync can re-case the selected artist's label -- the canonical
  // casing follows the catalog, which the sync itself writes. See
  // reconcileSelectedArtist; same handling as StockBrowser.
  useEffect(() => {
    const next = reconcileSelectedArtist(artists, selectedArtist)
    if (next === selectedArtist) return
    if (next) setSelectedArtist(next)
    else selectArtist('')
  }, [artists, selectedArtist])
  useEffect(() => { localStorage.setItem(`collectionViewMode_${scope}`, viewMode) }, [viewMode, scope])
  // The Price column and its sort header disappear when hasPriceField goes
  // false (e.g. a sync clears the user's last stored price) -- without this,
  // sort would stay pinned to discogs_price with no visible control claiming it.
  useEffect(() => {
    if (!hasPriceField && sort === 'discogs_price') {
      setSort('artist')
      setOrder('asc')
    }
  }, [hasPriceField, sort])

  function toggleSort(field: SortField) {
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

  // The card list has no column headers to click, so the same fields the
  // headers expose reach the same toggleSort through a select instead.
  const sortOptions: SortOption<SortField>[] = [
    { field: 'artist', label: 'Artist' },
    { field: 'title', label: 'Title' },
    { field: 'year', label: 'Year' },
    { field: 'label', label: 'Label' },
    { field: 'format', label: 'Format' },
    ...(hasPriceField ? [{ field: 'discogs_price', label: 'Price' } as SortOption<SortField>] : []),
    { field: 'date_added', label: 'Date Added' },
  ]
  const emptyMessage = scope === 'wantlist' ? WANTLIST_EMPTY : COLLECTION_EMPTY

  const sortButtonClass = 'w-full px-3 py-2 cursor-pointer hover:text-white select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-white/80'

  return (
    <div className="flex h-full overflow-hidden">
      {/* Sidebar. On a phone 192px is half the viewport spent permanently on a
          filter that is set once, so it moves into a sheet behind a toolbar
          button -- rendered instead of the sidebar, never alongside it. */}
      {!isMobile && (
        <ArtistSidebar artists={artists} selected={selectedArtist} onSelect={selectArtist} />
      )}

      {/* Main */}
      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        {/* Toolbar. One row on desktop; on mobile the search takes a row of its
            own and everything else wraps beneath it. `md:contents` dissolves
            the mobile grouping wrapper above the breakpoint, so the count and
            the control cluster stay direct children of the same flex row they
            are today. */}
        <div className="px-3 py-2 border-b border-gray-800 bg-gray-950 flex flex-col gap-2 md:flex-row md:items-center md:gap-0 md:px-4 md:py-3">
          {/* The count rides on the search line rather than the control line:
              it is the one thing here that is not a control, and giving it a
              row of its own cost the list a row of chrome. */}
          <div className="flex w-full items-center gap-3 md:contents">
            <div className="relative flex-1 md:w-full md:max-w-md md:flex-initial">
              <input
                type="text"
                placeholder="Search artist or title…"
                value={search}
                onChange={(e) => { setSearch(e.target.value); setPage(1) }}
                className={`w-full px-3 py-2 pr-8 text-sm md:py-1.5 ${textInputClass()}`}
              />
              <button
                onClick={() => { setSearch(''); setPage(1) }}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
              >
                ✕
              </button>
            </div>
            <span className="shrink-0 text-xs text-gray-500 md:ml-3 md:shrink">{total} records</span>
          </div>
          <div className="flex flex-wrap items-center gap-1.5 md:contents">
            {isMobile && (
              <ArtistSheetButton artists={artists} selected={selectedArtist} onSelect={selectArtist} />
            )}
            <div className="contents md:ml-auto md:flex md:items-center md:gap-1">
              {isMobile && viewMode === 'list' && (
                <MobileSort options={sortOptions} sort={sort} order={order} onSort={toggleSort} />
              )}
              {scope === 'collection' && (
                <select
                  value={unmatched ? 'unmatched' : 'all'}
                  onChange={(e) => { setUnmatched(e.target.value === 'unmatched'); setPage(1) }}
                  className={`px-3 py-2 text-sm md:py-1 ${selectClass()}`}
                >
                  <option value="all">All</option>
                  <option value="unmatched">Unmatched</option>
                </select>
              )}
              <button
                onClick={() => setViewMode('list')}
                title="List view"
                className={`w-11 h-11 flex items-center justify-center md:w-auto md:h-auto md:p-1.5 ${navButtonClass(viewMode === 'list')}`}
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
                className={`w-11 h-11 flex items-center justify-center md:w-auto md:h-auto md:p-1.5 ${navButtonClass(viewMode === 'tiles')}`}
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <rect x="2" y="2" width="5" height="5" />
                  <rect x="9" y="2" width="5" height="5" />
                  <rect x="2" y="9" width="5" height="5" />
                  <rect x="9" y="9" width="5" height="5" />
                </svg>
              </button>
              {onRefreshCollection && (
                <button
                  onClick={onRefreshCollection}
                  disabled={syncing}
                  title={scope === 'wantlist' ? 'Sync wantlist from Discogs' : 'Sync collection from Discogs'}
                  className={`w-11 h-11 flex items-center justify-center disabled:opacity-30 disabled:cursor-not-allowed md:w-auto md:h-auto md:p-1.5 ${navButtonClass(false)}`}
                >
                  <span className="block text-base leading-none">{syncing ? '⟳' : '↻'}</span>
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Tiles */}
        {viewMode === 'tiles' && (
          <div className="flex-1 overflow-auto" ref={tableScrollRef}>
            {hasLoaded && releases.length === 0 && (
              <div className="text-center py-8 px-4 text-gray-500">
                {emptyMessage}
              </div>
            )}
            {releases.length > 0 && (
              <div className="grid gap-3 p-3 md:gap-4 md:p-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))' }}>
                {releases.map((r) => (
                  <div key={r.discogs_id}>
                    <a href={r.discogs_url} target="_blank" rel="noreferrer" aria-label={`View ${r.artist} – ${r.title} on Discogs`}>
                      {r.cover_image_url ? (
                        <img
                          src={r.cover_image_url}
                          alt={r.title}
                          className="w-full aspect-square object-cover rounded"
                        />
                      ) : (
                        <div className="w-full aspect-square bg-gray-800 rounded" />
                      )}
                    </a>
                    <div className="mt-1.5 text-sm text-gray-200 truncate">{r.artist}</div>
                    {r.plex_url ? (
                      <a
                        href={r.plex_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-xs text-gray-400 truncate hover:text-white block"
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

        {/* Card list. Replaces the table below the breakpoint rather than
            side-scrolling it: artist and title are what a row *is*, and the
            other columns are annotations on that, which a card can say with
            hierarchy and a seven-column table two swipes wide cannot. */}
        {viewMode === 'list' && isMobile && (
          <div className="flex-1 overflow-auto" ref={tableScrollRef}>
            {hasLoaded && releases.length === 0 && (
              <div className="text-center py-8 px-4 text-gray-500">{emptyMessage}</div>
            )}
            <ul className="divide-y divide-gray-800">
              {releases.map((r) => {
                const meta = [
                  r.year ?? null,
                  r.label || null,
                  r.format || null,
                  hasPriceField ? r.discogs_price ?? null : null,
                ].filter(Boolean).join(' · ')
                return (
                  <li key={r.discogs_id} className="flex items-center gap-3 px-3 py-2 text-left">
                    <a
                      href={r.discogs_url}
                      target="_blank"
                      rel="noreferrer"
                      aria-label={`View ${r.artist} – ${r.title} on Discogs`}
                      className="shrink-0"
                    >
                      {r.cover_image_url ? (
                        <img src={r.cover_image_url} alt={r.title} className="w-14 h-14 object-cover rounded" />
                      ) : (
                        <div className="w-14 h-14 bg-gray-800 rounded" />
                      )}
                    </a>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm text-gray-200">{r.artist}</div>
                      {r.plex_url ? (
                        <a href={r.plex_url} target="_blank" rel="noreferrer" className="block truncate text-sm text-gray-300 hover:text-white">
                          {r.title}
                        </a>
                      ) : (
                        <div className="truncate text-sm text-gray-300">{r.title}</div>
                      )}
                      {meta && <div className="truncate text-xs text-gray-500">{meta}</div>}
                    </div>
                  </li>
                )
              })}
            </ul>
          </div>
        )}

        {/* Table */}
        {viewMode === 'list' && !isMobile && (
        <div className="flex-1 overflow-auto" ref={tableScrollRef}>
          <table className="w-full text-sm border-collapse">
            <thead className="sticky top-0 bg-gray-900 text-xs text-gray-400 uppercase">
              <tr>
                <th className="w-12 px-3 py-2"></th>
                <th
                  className="text-right"
                  aria-sort={sort === 'artist' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}
                >
                  <button type="button" onClick={() => toggleSort('artist')} className={`${sortButtonClass} text-right`}>
                    Artist {sort === 'artist' ? (order === 'asc' ? '↑' : '↓') : ''}
                  </button>
                </th>
                <th
                  className="text-left"
                  aria-sort={sort === 'title' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}
                >
                  <button type="button" onClick={() => toggleSort('title')} className={`${sortButtonClass} text-left`}>
                    Title {sort === 'title' ? (order === 'asc' ? '↑' : '↓') : ''}
                  </button>
                </th>
                <th
                  className="text-center"
                  aria-sort={sort === 'year' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}
                >
                  <button type="button" onClick={() => toggleSort('year')} className={`${sortButtonClass} text-center`}>
                    Year {sort === 'year' ? (order === 'asc' ? '↑' : '↓') : ''}
                  </button>
                </th>
                <th
                  className="text-center"
                  aria-sort={sort === 'label' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}
                >
                  <button type="button" onClick={() => toggleSort('label')} className={`${sortButtonClass} text-center`}>
                    Label {sort === 'label' ? (order === 'asc' ? '↑' : '↓') : ''}
                  </button>
                </th>
                <th
                  className="text-center"
                  aria-sort={sort === 'format' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}
                >
                  <button type="button" onClick={() => toggleSort('format')} className={`${sortButtonClass} text-center`}>
                    Format {sort === 'format' ? (order === 'asc' ? '↑' : '↓') : ''}
                  </button>
                </th>
                {hasPriceField && (
                  <th
                    className="text-center"
                    aria-sort={sort === 'discogs_price' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    <button type="button" onClick={() => toggleSort('discogs_price')} className={`${sortButtonClass} text-center`}>
                      Price {sort === 'discogs_price' ? (order === 'asc' ? '↑' : '↓') : ''}
                    </button>
                  </th>
                )}
                <th
                  className="text-center"
                  aria-sort={sort === 'date_added' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}
                >
                  <button type="button" onClick={() => toggleSort('date_added')} className={`${sortButtonClass} text-center`}>
                    Date Added {sort === 'date_added' ? (order === 'asc' ? '↑' : '↓') : ''}
                  </button>
                </th>
              </tr>
            </thead>
            <tbody>
              {hasLoaded && releases.length === 0 && (
                <tr>
                  <td colSpan={hasPriceField ? 8 : 7} className="text-center py-8 text-gray-500">
                    {emptyMessage}
                  </td>
                </tr>
              )}
              {releases.map((r) => (
                <tr key={r.discogs_id} className="border-t border-gray-800 hover:bg-gray-900/50">
                  <td className="px-3 py-2">
                    <a href={r.discogs_url} target="_blank" rel="noreferrer" aria-label={`View ${r.artist} – ${r.title} on Discogs`}>
                      {r.cover_image_url ? (
                        <img
                          src={r.cover_image_url}
                          alt={r.title}
                          className="w-10 h-10 min-w-10 object-cover rounded"
                        />
                      ) : (
                        <div className="w-10 h-10 bg-gray-800 rounded" />
                      )}
                    </a>
                  </td>
                  <td className="px-3 py-2 text-right text-gray-200">
                    {r.artist}
                  </td>
                  <td className="px-3 py-2 text-left text-gray-300">
                    {r.plex_url ? (
                      <a href={r.plex_url} target="_blank" rel="noreferrer" className="hover:text-white">
                        {r.title}
                      </a>
                    ) : (
                      r.title
                    )}
                  </td>
                  <td className="px-3 py-2 text-gray-400">{r.year ?? '—'}</td>
                  <td className="px-3 py-2 text-gray-400 truncate max-w-32">{r.label}</td>
                  <td className="px-3 py-2 text-gray-400">{r.format}</td>
                  {hasPriceField && <td className="px-3 py-2 text-gray-400">{r.discogs_price ?? '—'}</td>}
                  <td className="px-3 py-2 text-gray-400">
                    {r.date_added ? new Date(r.date_added).toLocaleDateString() : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="border-t border-gray-800 px-4 py-2 flex items-center justify-center gap-2 text-sm text-gray-400 md:justify-start">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className={`px-4 py-3 disabled:opacity-40 md:px-2 md:py-1 ${dismissButtonClass()}`}
            >
              ← Prev
            </button>
            <span>Page {page} of {totalPages}</span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className={`px-4 py-3 disabled:opacity-40 md:px-2 md:py-1 ${dismissButtonClass()}`}
            >
              Next →
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
