import { useState, useEffect, useCallback, useRef } from 'react'
import { getReleases, getArtists } from '../api/client'
import type { Release, SortField, SortOrder, RecordScope } from '../api/types'
import { navButtonClass, dismissButtonClass } from '../styles/buttons'
import { textInputClass, selectClass } from '../styles/inputs'

interface Props {
  scope: RecordScope
  syncing?: boolean
  onRefreshCollection?: () => void
  syncGeneration?: number
}

// Rendered from both the tile and table branches below, which would otherwise
// hold identical copies that drift apart.
const WANTLIST_EMPTY = 'No wantlist items yet. Add records to your wantlist on Discogs, then sync.'
const COLLECTION_EMPTY = 'No records found. Click the sync icon above to load your collection from Discogs.'

export default function RecordBrowser({ scope, syncing, onRefreshCollection, syncGeneration }: Props) {
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

  const load = useCallback(async () => {
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
    setReleases(result.releases)
    setTotal(result.total)
    setHasLoaded(true)
  }, [search, selectedArtist, sort, order, page, scope, unmatched])

  useEffect(() => { load() }, [load])
  // syncGeneration ticks on every sync_progress/sync_complete SSE event so the
  // collection/wantlist tables fill in as pages land, not just once the whole
  // sync finishes. Guarded on truthy (not just changed) so the initial render's
  // generation of 0 doesn't trigger a redundant second load alongside the
  // mount effect above.
  useEffect(() => {
    if (syncGeneration) load()
  }, [syncGeneration, load])
  // Also refetches on syncGeneration ticks, same as load() above -- otherwise
  // the nav list stays stuck at whatever it was on mount while a collection
  // sync fills the table in page by page.
  useEffect(() => { getArtists(scope).then(setArtists) }, [scope, syncGeneration])
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
          <span className="ml-3 text-xs text-gray-500">{total} records</span>
          <div className="ml-auto flex items-center gap-1">
            {scope === 'collection' && (
              <select
                value={unmatched ? 'unmatched' : 'all'}
                onChange={(e) => { setUnmatched(e.target.value === 'unmatched'); setPage(1) }}
                className={`px-3 py-1 text-sm ${selectClass()}`}
              >
                <option value="all">All</option>
                <option value="unmatched">Unmatched</option>
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
            {onRefreshCollection && (
              <button
                onClick={onRefreshCollection}
                disabled={syncing}
                title={scope === 'wantlist' ? 'Sync wantlist from Discogs' : 'Sync collection from Discogs'}
                className={`p-1.5 disabled:opacity-30 disabled:cursor-not-allowed ${navButtonClass(false)}`}
              >
                <span className="block text-base leading-none">{syncing ? '⟳' : '↻'}</span>
              </button>
            )}
          </div>
        </div>

        {/* Tiles */}
        {viewMode === 'tiles' && (
          <div className="flex-1 overflow-auto" ref={tableScrollRef}>
            {hasLoaded && releases.length === 0 && (
              <div className="text-center py-8 text-gray-500">
                {scope === 'wantlist' ? WANTLIST_EMPTY : COLLECTION_EMPTY}
              </div>
            )}
            {releases.length > 0 && (
              <div className="grid gap-4 p-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))' }}>
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

        {/* Table */}
        {viewMode === 'list' && (
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
                <th
                  className="text-center"
                  aria-sort={sort === 'discogs_price' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}
                >
                  <button type="button" onClick={() => toggleSort('discogs_price')} className={`${sortButtonClass} text-center`}>
                    Price {sort === 'discogs_price' ? (order === 'asc' ? '↑' : '↓') : ''}
                  </button>
                </th>
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
                  <td colSpan={8} className="text-center py-8 text-gray-500">
                    {scope === 'wantlist' ? WANTLIST_EMPTY : COLLECTION_EMPTY}
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
                  <td className="px-3 py-2 text-gray-400">{r.discogs_price ?? '—'}</td>
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
          <div className="border-t border-gray-800 px-4 py-2 flex items-center gap-2 text-sm text-gray-400">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className={`px-2 py-1 disabled:opacity-40 ${dismissButtonClass()}`}
            >
              ← Prev
            </button>
            <span>Page {page} of {totalPages}</span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className={`px-2 py-1 disabled:opacity-40 ${dismissButtonClass()}`}
            >
              Next →
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
