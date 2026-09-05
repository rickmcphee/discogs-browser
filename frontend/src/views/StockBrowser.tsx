import { useState, useEffect, useCallback, useRef, memo } from 'react'
import { getStock, getStockArtists, saveStockItem, unsaveStockItem } from '../api/client'
import type { StockItem, StockSortField, SortOrder, StockScope, LibraryScope, Crawler } from '../api/types'
import { navButtonClass, dismissButtonClass } from '../styles/buttons'
import { textInputClass, selectClass } from '../styles/inputs'
import { reconcileSelectedArtist } from './artistSelection'
import SourceFilter from '../components/SourceFilter'
import StockStats from '../components/StockStats'
import { formatPrice } from './formatPrice'
import { useIsMobile } from '../hooks/useMediaQuery'
import { ArtistSidebar, ArtistSheetButton } from '../components/ArtistFilter'
import MobileSort, { type SortOption } from '../components/MobileSort'

interface Props {
  scope?: StockScope
  recommendedAvailable?: boolean
  hiddenCrawlerIds?: number[]
  crawlers?: Crawler[]
  onHiddenCrawlerIdsChange?: (hiddenCrawlerIds: number[]) => void
  hiddenCrawlerIdsLoaded?: boolean
  syncGeneration?: number
  /** Strict subsets of syncGeneration -- see App.tsx. The item list needs the
   *  union (its comparison rows are listings); the Stats panel counts
   *  stock_items and takes only what can actually move that count. */
  inventoryGeneration?: number
  judgmentGeneration?: number
  isAdmin?: boolean
  hasPriceField?: boolean
}

const NO_HIDDEN_CRAWLER_IDS: number[] = []
const NO_CRAWLERS: Crawler[] = []
const NOOP_HIDDEN_CRAWLER_IDS_CHANGE = () => {}
const STORE_FILTERS = ['all', 'recommended', 'saved', 'overlapped'] as const
const TRACK_FILTERS = ['all', 'collection', 'wantlist'] as const satisfies readonly LibraryScope[]

// The name shown for a row is what the source called the item when the
// crawler reported one, since a release-crawler match is by artist/title and
// can be a different pressing than the target. The target's own title moves
// to the hover text so the substitution stays visible; a recommendation
// reason keeps that slot when there is one. The thumbnail's alt text is not
// substituted: the image is the target's own cover, not the listing's.
function displayTitle(item: StockItem): string {
  return item.listing_title ?? item.title
}

function titleTooltip(item: StockItem): string | undefined {
  if (item.reason) return item.reason
  if (item.listing_title && item.listing_title !== item.title) return item.title
  return undefined
}

function trackLibraryScope(value: string): LibraryScope | undefined {
  return (TRACK_FILTERS as readonly string[]).includes(value) ? (value as LibraryScope) : undefined
}

function BookmarkIcon({ filled }: { filled: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill={filled ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.5">
      <path d="M4 2h8a1 1 0 0 1 1 1v11l-5-3-5 3V3a1 1 0 0 1 1-1Z" strokeLinejoin="round" />
    </svg>
  )
}

function StockBrowser({
  scope = 'store', recommendedAvailable = false, hiddenCrawlerIds = NO_HIDDEN_CRAWLER_IDS,
  crawlers = NO_CRAWLERS, onHiddenCrawlerIdsChange = NOOP_HIDDEN_CRAWLER_IDS_CHANGE,
  hiddenCrawlerIdsLoaded = true, syncGeneration, inventoryGeneration, judgmentGeneration,
  isAdmin = false, hasPriceField = true,
}: Props) {
  const isMobile = useIsMobile()
  const [items, setItems] = useState<StockItem[]>([])
  const [total, setTotal] = useState(0)
  // Rows in the paginated set -- what pages divide by. A Cost sort in list
  // view flattens each item's comparison rows into one ordering, so a page
  // there holds PER_PAGE rows rather than PER_PAGE items; every other request
  // pages by item, where this just matches `total`. `total` still drives the
  // "N items" label, which has to keep agreeing with the Stats breakdown.
  const [rowTotal, setRowTotal] = useState(0)
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
  // Store only: one record, one row, the cheapest store's. Track is left
  // alone -- it reads as "every place this record I follow is in stock", and
  // hiding the dearer stores there hides half the answer.
  const [cheapest, setCheapest] = useState(
    () => scope === 'store' && localStorage.getItem('stockCheapest') === 'true'
  )
  const [hasLoaded, setHasLoaded] = useState(false)
  // Bumped after every toggleSaved attempt (success or failure) to trigger a
  // race-guarded refetch through the same effects load()/getStockArtists
  // already run under -- see toggleSaved.
  const [retryTick, setRetryTick] = useState(0)
  // item_keys with a save/unsave request currently in flight. Guards against
  // the reversed-completion-order race: a second click on the same item_key
  // while its first request is still pending is a no-op (both in toggleSaved
  // and via the disabled button), so at most one request per item_key is ever
  // outstanding and there is nothing left to reconcile out of order.
  const [pendingSaves, setPendingSaves] = useState<Set<string>>(new Set())
  const PER_PAGE = 250
  const tableScrollRef = useRef<HTMLDivElement>(null)

  const [prevHiddenCrawlerIds, setPrevHiddenCrawlerIds] = useState(hiddenCrawlerIds)
  if (hiddenCrawlerIds !== prevHiddenCrawlerIds) {
    setPrevHiddenCrawlerIds(hiddenCrawlerIds)
    setPage(1)
  }

  // The two views don't page over the same thing under a Cost sort: list gets
  // the flattened offer rows, tiles only the items. Switching while deep in
  // one can land past the end of the other, so both start over -- the same
  // reset the hidden-crawler change above does, for the same reason.
  const [prevViewMode, setPrevViewMode] = useState(viewMode)
  if (viewMode !== prevViewMode) {
    setPrevViewMode(viewMode)
    setPage(1)
  }

  // isLatest gates the commit rather than the request: reconciliation can clear
  // or re-case the selection while a request started under the old one is still
  // in flight, and that older filtered response arriving last would leave the
  // table showing a subset the sidebar no longer claims to be filtering by.
  const load = useCallback(async (isLatest: () => boolean = () => true) => {
    // Until the caller's hidden-crawler set has actually loaded (App.tsx
    // starts it false, flips it true once GET /api/user-hidden-crawlers
    // resolves), hiddenCrawlerIds is a placeholder [] -- fetching now would
    // briefly render items from a source the user has hidden, or do so
    // indefinitely if that GET never resolves. Skip the request entirely;
    // this effect re-runs once hiddenCrawlerIdsLoaded flips true.
    if (!hiddenCrawlerIdsLoaded) return
    const result = await getStock({
      search: search || undefined,
      artist: selectedArtist || undefined,
      sort, order, page, per_page: PER_PAGE,
      libraryScope: scope === 'track' ? trackLibraryScope(filter) : undefined,
      recommended: scope === 'store' && filter === 'recommended',
      saved: scope === 'store' && filter === 'saved',
      overlapped: scope === 'store' && filter === 'overlapped',
      hiddenCrawlerIds,
      cheapest: scope === 'store' && cheapest,
      // Tiles render own rows only, so asking for comparison rows there would
      // spend a whole page of the flattened Cost ordering on rows the grid
      // then drops -- leaving it near-empty. Grouped sorts are unaffected
      // either way; this just stops fetching what tiles never show.
      includeComparisons: viewMode === 'list',
    })
    if (!isLatest()) return
    setItems(result.items)
    setTotal(result.total)
    setRowTotal(result.row_total)
    setHasLoaded(true)
  }, [search, selectedArtist, sort, order, page, filter, hiddenCrawlerIds, scope, hiddenCrawlerIdsLoaded, viewMode, cheapest])

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
  }, [load, syncGeneration, retryTick])
  useEffect(() => {
    if (!recommendedAvailable && filter === 'recommended') {
      setFilter('all')
    }
  }, [recommendedAvailable, filter])
  // Same hazard as changeFilter's discogs_price reset above, but for the
  // hasPriceField prop itself flipping false (e.g. a sync clears the user's
  // last price) rather than a user-driven filter change.
  useEffect(() => {
    if (!hasPriceField && sort === 'discogs_price') {
      setSort('artist')
      setOrder('asc')
    }
  }, [hasPriceField, sort])
  // Also refetches on syncGeneration ticks, same as load() above -- otherwise
  // the sidebar's artist list would go stale mid-crawl.
  useEffect(() => {
    // Same hiddenCrawlerIdsLoaded gate as load() above, and for the same
    // reason: hiddenCrawlerIds is a placeholder [] until the real set loads.
    if (!hiddenCrawlerIdsLoaded) return
    // syncGeneration ticks faster than a request round-trip, so these overlap.
    // Committing whichever response lands last would let a stale list drive
    // the reconciliation below -- re-casing the selection to an old label, or
    // clearing an artist the newest response still lists.
    let latest = true
    getStockArtists({
      libraryScope: scope === 'track' ? trackLibraryScope(filter) : undefined,
      recommended: scope === 'store' && filter === 'recommended',
      saved: scope === 'store' && filter === 'saved',
      overlapped: scope === 'store' && filter === 'overlapped',
      hiddenCrawlerIds,
    }).then((list) => { if (latest) setArtists(list) })
    return () => { latest = false }
  }, [scope, filter, hiddenCrawlerIds, syncGeneration, retryTick, hiddenCrawlerIdsLoaded])
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
  useEffect(() => {
    if (scope === 'store') localStorage.setItem('stockCheapest', String(cheapest))
  }, [cheapest, scope])
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

  async function toggleSaved(item: StockItem) {
    // A quick save-then-unsave (or vice versa) before the first request
    // settles would fire two independent, unordered requests for the same
    // item_key -- whichever commits last on the server wins, which may not
    // match the user's actual last click. Rather than queue/replace, the
    // second click while one is in flight is a no-op: at most one request
    // per item_key is ever outstanding, so there's no completion order to
    // reconcile. The disabled button (see render) backs this up visually.
    if (pendingSaves.has(item.item_key)) return
    setPendingSaves((prev) => new Set(prev).add(item.item_key))
    const next = !item.saved
    setItems((prev) => {
      const patched = prev.map((it) => (it.item_key === item.item_key ? { ...it, saved: next } : it))
      return filter === 'saved' && !next ? patched.filter((it) => it.item_key !== item.item_key) : patched
    })
    if (filter === 'saved' && !next) setTotal((t) => t - 1)
    try {
      // Bumping retryTick -- rather than calling load()/getStockArtists
      // directly -- routes the refetch through the same isLatest-guarded
      // effects every other trigger already uses, so a request that resolves
      // after the user has since changed filter/search/sort/page can't
      // clobber a newer response. This runs on both success and failure: a
      // failure needs the items list to self-correct (undo the optimistic
      // patch), and a success needs the Saved-filter artist sidebar to drop
      // an artist whose last saved item was just unsaved.
      await (next ? saveStockItem(item.item_key) : unsaveStockItem(item.item_key)).catch(() => {})
    } finally {
      setPendingSaves((prev) => {
        const nextSet = new Set(prev)
        nextSet.delete(item.item_key)
        return nextSet
      })
      setRetryTick((t) => t + 1)
    }
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

  const totalPages = Math.ceil(rowTotal / PER_PAGE)
  const colCount = scope === 'track' ? (hasPriceField ? 7 : 6) : 7
  const priceSortable = scope === 'track' && filter !== 'wantlist'
  const emptyMessage =
    scope === 'store' && filter === 'recommended' ? 'Nothing recommended is in stock right now.'
    : scope === 'store' && filter === 'saved' ? "You haven't saved anything yet."
    : scope === 'store' && filter === 'overlapped' ? 'Nothing by an artist in your collection is in stock right now.'
    : scope === 'store' ? (isAdmin ? 'No in-stock items yet. Click Refresh under Store Management in Settings.' : 'No in-stock items yet. Check back after the next store sync.')
    : filter === 'collection' ? 'Nothing in your collection is in stock right now.'
    : filter === 'wantlist' ? 'Nothing on your wantlist is in stock right now.'
    : "Nothing you're tracking is in stock right now."

  // Mirrors the column headers below, gated the same way: the discogs price is
  // only a column, and only sortable, where the table shows one.
  const sortOptions: SortOption<StockSortField>[] = [
    { field: 'artist', label: 'Artist' },
    { field: 'title', label: 'Title' },
    { field: 'format', label: 'Format' },
    ...(scope === 'track' && hasPriceField && priceSortable
      ? [{ field: 'discogs_price', label: 'Price' } as SortOption<StockSortField>]
      : []),
    { field: 'price', label: 'Cost' },
    { field: 'source', label: 'Source' },
  ]

  const sortButtonClass = 'w-full px-3 py-2 cursor-pointer hover:text-white select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-white/80'

  return (
    <div className="flex h-full overflow-hidden">
      {/* Sidebar. Same trade as RecordBrowser's: on a phone it becomes a sheet
          behind a toolbar button, rendered instead of the sidebar. */}
      {!isMobile && (
        <ArtistSidebar artists={artists} selected={selectedArtist} onSelect={selectArtist} />
      )}

      {/* Main */}
      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        {/* Toolbar. Same shape as RecordBrowser's: one row on desktop, search
            on its own row with everything else wrapping beneath it on mobile,
            and `md:contents` dissolving the mobile grouping wrapper above the
            breakpoint. */}
        <div className="px-3 py-2 border-b border-gray-800 bg-gray-950 flex flex-col gap-2 md:flex-row md:items-center md:gap-0 md:px-4 md:py-3">
          {/* The count rides on the search line rather than the control line:
              it is the one thing here that is not a control, and giving it a
              row of its own cost the list a row of chrome. Store omits it --
              the Stats button beside the toolbar already surfaces the total. */}
          <div className="flex w-full items-center gap-3 md:contents">
            <div className="relative flex-1 md:w-full md:max-w-md md:flex-initial">
              <input
                type="text"
                placeholder="Search artist or title…"
                value={search}
                onChange={(e) => { setSearch(e.target.value); setPage(1) }}
                className={`w-full px-3 py-2 pr-11 text-sm md:py-1.5 md:pr-8 ${textInputClass()}`}
              />
              <button
                onClick={() => { setSearch(''); setPage(1) }}
                aria-label="Clear search"
                className="absolute right-0 top-1/2 flex h-11 w-11 -translate-y-1/2 items-center justify-center text-gray-500 hover:text-gray-300 md:right-3 md:h-auto md:w-auto"
              >
                <span aria-hidden="true">✕</span>
              </button>
            </div>
            {scope !== 'store' && (
              <span className="shrink-0 text-xs text-gray-500 md:ml-3 md:shrink">{total} items</span>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-1.5 md:contents">
            {isMobile && (
              <ArtistSheetButton artists={artists} selected={selectedArtist} onSelect={selectArtist} />
            )}
            <div className="contents md:ml-auto md:flex md:items-center md:gap-2">
              {isMobile && viewMode === 'list' && (
                <MobileSort options={sortOptions} sort={sort} order={order} onSort={toggleSort} />
              )}
              <SourceFilter crawlers={crawlers} hiddenCrawlerIds={hiddenCrawlerIds} onChange={onHiddenCrawlerIdsChange} disabled={!hiddenCrawlerIdsLoaded} />
              {scope === 'store' && (
                <StockStats
                  search={search || undefined}
                  artist={selectedArtist || undefined}
                  recommended={filter === 'recommended'}
                  saved={filter === 'saved'}
                  overlapped={filter === 'overlapped'}
                  cheapest={cheapest}
                  hiddenCrawlerIds={hiddenCrawlerIds}
                  // Deliberately narrower than the list's syncGeneration: a
                  // listing_changed writes listings, not stock_items, and is
                  // broadcast to every connected user, so riding the union
                  // would fire a grouped count per marketplace write for every
                  // open panel. Judgments only move the Recommended filter, so
                  // they are added only there; retryTick covers save/unsave,
                  // which moves what Saved holds. Summed because any of them
                  // ticking has to refetch.
                  refreshKey={
                    (inventoryGeneration ?? 0)
                    + (filter === 'recommended' ? (judgmentGeneration ?? 0) : 0)
                    + retryTick
                  }
                  disabled={!hiddenCrawlerIdsLoaded}
                />
              )}
              <select
                value={filter}
                onChange={(e) => changeFilter(e.target.value)}
                className={`px-3 py-2 text-sm md:py-1 ${selectClass()}`}
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
                    <option value="saved">Saved</option>
                    <option value="overlapped">Overlapped</option>
                  </>
                )}
              </select>
              {scope === 'store' && (
                <label className="flex h-11 select-none items-center gap-1.5 px-1 text-sm text-gray-300 hover:text-white md:h-auto">
                  <input
                    type="checkbox"
                    checked={cheapest}
                    onChange={(e) => { setCheapest(e.target.checked); setPage(1) }}
                    className="accent-white"
                  />
                  Cheapest
                </label>
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
            </div>
          </div>
        </div>

        {/* Tiles */}
        {viewMode === 'tiles' && (
          <div className="flex-1 overflow-auto" ref={tableScrollRef}>
            {hasLoaded && items.length === 0 && (
              <div className="text-center py-8 px-4 text-gray-500 md:px-0">
                {emptyMessage}
              </div>
            )}
            {items.length > 0 && (
              <div className="grid gap-3 p-3 md:gap-4 md:p-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))' }}>
                {items.filter((item) => item.is_own).map((item) => (
                  <a
                    key={item.id}
                    href={item.url}
                    target="_blank"
                    rel="noreferrer"
                    className="group"
                  >
                    <div className="relative">
                      {item.cover_image_url ? (
                        <img
                          src={item.cover_image_url}
                          alt={item.title}
                          className="w-full aspect-square object-cover rounded"
                        />
                      ) : (
                        <div className="w-full aspect-square bg-gray-800 rounded" />
                      )}
                      {scope === 'store' && (
                        <button
                          onClick={(e) => { e.preventDefault(); toggleSaved(item) }}
                          title={item.saved ? 'Remove from saved' : 'Save for later'}
                          disabled={pendingSaves.has(item.item_key)}
                          className="absolute top-1 right-1 flex h-11 w-11 items-center justify-center rounded-full bg-gray-950/70 text-white hover:bg-gray-950 disabled:opacity-40 md:h-auto md:w-auto md:p-1"
                        >
                          <BookmarkIcon filled={item.saved} />
                        </button>
                      )}
                    </div>
                    <div className="mt-1.5 text-sm text-gray-200 truncate group-hover:text-white" title={item.reason ?? undefined}>{item.artist}</div>
                    <div className="text-xs text-gray-400 truncate" title={titleTooltip(item)}>{displayTitle(item)}</div>
                  </a>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Card list -- see RecordBrowser for why cards rather than a
            side-scrolling table. The cost link and the save button stay on the
            right, where they are the row's two actions. */}
        {viewMode === 'list' && isMobile && (
          <div className="flex-1 overflow-auto" ref={tableScrollRef}>
            {hasLoaded && items.length === 0 && (
              <div className="text-center py-8 px-4 text-gray-500">{emptyMessage}</div>
            )}
            <ul className="divide-y divide-gray-800">
              {items.map((item) => {
                const meta = [
                  item.format || null,
                  item.source || null,
                  // "Price" is the discogs price, as in the table header; the
                  // link on the right is "Cost", what this store wants for it.
                  scope === 'track' && hasPriceField && item.discogs_price ? `Price ${item.discogs_price}` : null,
                ].filter(Boolean).join(' · ')
                return (
                  <li key={item.id} className="flex items-center gap-3 px-3 py-2 text-left">
                    {item.cover_image_url ? (
                      <img src={item.cover_image_url} alt="" className="w-14 h-14 shrink-0 object-cover rounded" />
                    ) : (
                      <div className="w-14 h-14 shrink-0 bg-gray-800 rounded" />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm text-gray-200" title={item.reason ?? undefined}>{item.artist}</div>
                      <div className="truncate text-sm text-gray-300" title={titleTooltip(item)}>{displayTitle(item)}</div>
                      {meta && <div className="truncate text-xs text-gray-500">{meta}</div>}
                      {item.reason && (
                        <div className="text-xs italic text-gray-500">{item.reason}</div>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <a href={item.url} target="_blank" rel="noreferrer" className="px-2 py-3 text-sm font-medium text-green-400 hover:text-green-300">
                        {item.price != null ? formatPrice(item.price, item.currency) : 'View'}
                      </a>
                      {scope === 'store' && (
                        <button
                          onClick={() => toggleSaved(item)}
                          title={item.saved ? 'Remove from saved' : 'Save for later'}
                          disabled={pendingSaves.has(item.item_key)}
                          className={`w-11 h-11 flex items-center justify-center disabled:opacity-40 ${dismissButtonClass()}`}
                        >
                          <BookmarkIcon filled={item.saved} />
                        </button>
                      )}
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
                {scope === 'track' && hasPriceField && (
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
                  <button type="button" onClick={() => toggleSort('source')} aria-label="Sort by source" className={`${sortButtonClass} text-center`}>
                    Source {sort === 'source' ? (order === 'asc' ? '↑' : '↓') : ''}
                  </button>
                </th>
                {scope === 'store' && <th className="w-8 px-3 py-2"></th>}
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
                  <td className="px-3 py-2 text-left text-gray-300" title={titleTooltip(item)}>{displayTitle(item)}</td>
                  <td className="px-3 py-2 text-gray-400">{item.format ?? '—'}</td>
                  {scope === 'track' && hasPriceField && (
                    <td className="px-3 py-2 text-gray-400">{item.discogs_price ?? '—'}</td>
                  )}
                  <td className="px-3 py-2">
                    <a href={item.url} target="_blank" rel="noreferrer" className="text-green-400 hover:text-green-300 font-medium">
                      {item.price != null ? formatPrice(item.price, item.currency) : 'View'}
                    </a>
                  </td>
                  <td className="px-3 py-2 text-gray-400">{item.source}</td>
                  {scope === 'store' && (
                    <td className="px-3 py-2">
                      <button
                        onClick={() => toggleSaved(item)}
                        title={item.saved ? 'Remove from saved' : 'Save for later'}
                        disabled={pendingSaves.has(item.item_key)}
                        className={`p-1 disabled:opacity-40 ${dismissButtonClass()}`}
                      >
                        <BookmarkIcon filled={item.saved} />
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="border-t border-gray-800 px-4 py-2 flex items-center justify-center gap-2 text-sm text-gray-400 md:justify-start">
            <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1} className={`px-4 py-3 disabled:opacity-40 md:px-2 md:py-1 ${dismissButtonClass()}`}>← Prev</button>
            <span>Page {page} of {totalPages}</span>
            <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page === totalPages} className={`px-4 py-3 disabled:opacity-40 md:px-2 md:py-1 ${dismissButtonClass()}`}>Next →</button>
          </div>
        )}
      </div>
    </div>
  )
}

export default memo(StockBrowser)
