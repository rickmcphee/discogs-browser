import { useState, useEffect, useCallback, useRef } from 'react'
import { getStock, getStockArtists } from '../api/client'
import type { StockItem, StockSortField, SortOrder } from '../api/types'

export default function StockBrowser() {
  const [items, setItems] = useState<StockItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [selectedArtist, setSelectedArtist] = useState('')
  const [artists, setArtists] = useState<string[]>([])
  const [sort, setSort] = useState<StockSortField>('artist')
  const [order, setOrder] = useState<SortOrder>('asc')
  const [filter, setFilter] = useState<'all' | 'overlapping' | 'recommended'>(
    () => (localStorage.getItem('stockFilter') === 'overlapping' ? 'overlapping' : 'all')
  )
  const [loading, setLoading] = useState(false)
  const [viewMode, setViewMode] = useState<'list' | 'tiles'>(
    () => (localStorage.getItem('collectionViewMode_instock') === 'tiles' ? 'tiles' : 'list')
  )
  const PER_PAGE = 250
  const tableScrollRef = useRef<HTMLDivElement>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await getStock({
        search: search || undefined,
        artist: selectedArtist || undefined,
        sort, order, page, per_page: PER_PAGE,
        overlapping: filter === 'overlapping',
      })
      setItems(result.items)
      setTotal(result.total)
    } finally {
      setLoading(false)
    }
  }, [search, selectedArtist, sort, order, page, filter])

  useEffect(() => { load() }, [load])
  useEffect(() => { getStockArtists(filter === 'overlapping').then(setArtists) }, [filter])
  useEffect(() => { localStorage.setItem('collectionViewMode_instock', viewMode) }, [viewMode])
  useEffect(() => { localStorage.setItem('stockFilter', filter) }, [filter])
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
          <span className="ml-3 text-xs text-gray-500">{total} items</span>
          <div className="ml-auto flex items-center gap-2">
            <select
              value={filter}
              onChange={(e) => { setFilter(e.target.value as 'all' | 'overlapping' | 'recommended'); setPage(1) }}
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-indigo-500"
            >
              <option value="all">All</option>
              <option value="overlapping">Overlapping</option>
              <option value="recommended" disabled>Recommended</option>
            </select>
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
            {!loading && items.length === 0 && (
              <div className="text-center py-8 text-gray-500">
                No in-stock items yet. Click "Refresh Stock Now" in Settings.
              </div>
            )}
            {!loading && items.length > 0 && (
              <div className="grid gap-4 p-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))' }}>
                {items.map((item) => (
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
                    <div className="mt-1.5 text-sm text-gray-200 truncate group-hover:text-indigo-400">{item.artist}</div>
                    <div className="text-xs text-gray-400 truncate">{item.title}</div>
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
                <th className="px-3 py-2 text-center cursor-pointer hover:text-white select-none" onClick={() => toggleSort('artist')}>
                  Artist {sort === 'artist' ? (order === 'asc' ? '↑' : '↓') : ''}
                </th>
                <th className="px-3 py-2 text-center cursor-pointer hover:text-white select-none" onClick={() => toggleSort('title')}>
                  Title {sort === 'title' ? (order === 'asc' ? '↑' : '↓') : ''}
                </th>
                <th className="px-3 py-2 text-center cursor-pointer hover:text-white select-none" onClick={() => toggleSort('format')}>
                  Format {sort === 'format' ? (order === 'asc' ? '↑' : '↓') : ''}
                </th>
                <th className="px-3 py-2 text-center cursor-pointer hover:text-white select-none" onClick={() => toggleSort('price')}>
                  Price {sort === 'price' ? (order === 'asc' ? '↑' : '↓') : ''}
                </th>
                <th className="px-3 py-2 text-center">Source</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={6} className="text-center py-8 text-gray-500">Loading…</td></tr>
              )}
              {!loading && items.length === 0 && (
                <tr><td colSpan={6} className="text-center py-8 text-gray-500">No in-stock items yet. Click "Refresh Stock Now" in Settings.</td></tr>
              )}
              {items.map((item) => (
                <tr key={item.id} className="border-t border-gray-800 hover:bg-gray-900/50">
                  <td className="px-3 py-2">
                    {item.cover_image_url ? (
                      <img
                        src={item.cover_image_url}
                        alt={item.title}
                        className="w-10 h-10 object-cover rounded"
                      />
                    ) : (
                      <div className="w-10 h-10 bg-gray-800 rounded" />
                    )}
                  </td>
                  <td className="px-3 py-2 text-gray-200">{item.artist}</td>
                  <td className="px-3 py-2 text-gray-300">{item.title}</td>
                  <td className="px-3 py-2 text-gray-400">{item.format ?? '—'}</td>
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
            <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1} className="px-2 py-1 rounded hover:bg-gray-800 disabled:opacity-40">← Prev</button>
            <span>Page {page} of {totalPages}</span>
            <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page === totalPages} className="px-2 py-1 rounded hover:bg-gray-800 disabled:opacity-40">Next →</button>
          </div>
        )}
      </div>
    </div>
  )
}
