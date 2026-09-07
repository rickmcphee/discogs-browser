import { useState, useEffect, useCallback, useLayoutEffect, useRef, memo, type MouseEvent as ReactMouseEvent } from 'react'
import { getStock, getStockArtists, saveStockItem, unsaveStockItem } from '../api/client'
import type { StockItem, StockSortField, SortOrder, LibraryScope, Crawler } from '../api/types'
import { navButtonClass, dismissButtonClass } from '../styles/buttons'
import { textInputClass } from '../styles/inputs'
import { reconcileSelectedArtist } from './artistSelection'
import SourceFilter from '../components/SourceFilter'
import StockStats from '../components/StockStats'
import StockFilter from '../components/StockFilter'
import { formatPrice } from './formatPrice'
import { placeReasonPopover, reasonPopoverMaxWidth, type Insets, type Placement } from './reasonPopoverPosition'
import { useIsMobile } from '../hooks/useMediaQuery'
import { ArtistSidebar, ArtistSheetButton } from '../components/ArtistFilter'
import MobileSort, { type SortOption } from '../components/MobileSort'

interface Props {
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
  /** Ticks on collection-sync events. Only the filters that read
   *  library_items (Collection, Wantlist, Overlapped, Recommended) can have a
   *  row moved by one, so it drives a refetch only while one of them is
   *  active -- this pane stays mounted while hidden. */
  libraryGeneration?: number
  isAdmin?: boolean
  hasPriceField?: boolean
}

const NO_HIDDEN_CRAWLER_IDS: number[] = []
const NO_CRAWLERS: Crawler[] = []
const NOOP_HIDDEN_CRAWLER_IDS_CHANGE = () => {}
const STORE_FILTERS = ['all', 'recommended', 'saved', 'overlapped', 'collection', 'wantlist'] as const
const LIBRARY_DEPENDENT_FILTERS: ReadonlySet<string> = new Set(['collection', 'wantlist', 'overlapped', 'recommended'])
// Names the control, not its content: the justification itself is behind the
// click now, so a hover that gave it away would be the tooltip all over again.
const REASON_BUTTON_TITLE = 'Recommendation details'
// Only one popover is open at a time, so a single id is enough for the
// aria-describedby that ties it to the icon it belongs to.
const REASON_PANEL_ID = 'stock-reason-popover'

// The name shown for a row is what the source called the item when the
// crawler reported one, since a release-crawler match is by artist/title and
// can be a different pressing than the target. The target's own title moves
// to the hover text so the substitution stays visible -- it has that slot to
// itself now that a judgment's reason is read from the info popup rather than
// a tooltip. The thumbnail's alt text is not substituted: the image is the
// target's own cover, not the listing's.
function displayTitle(item: StockItem): string {
  return item.listing_title ?? item.title
}

// True when the row's visible name is the source's rather than the target's:
// a release crawler matches by artist and title, so what it found can be a
// different pressing -- or, where the match was loose, a different record.
function namesAnotherPressing(item: StockItem): boolean {
  return !!item.listing_title && item.listing_title !== item.title
}

function titleTooltip(item: StockItem): string | undefined {
  return namesAnotherPressing(item) ? item.title : undefined
}

// Collection and Wantlist narrow to the user's library at release level; the
// other filters send no scope at all.
function libraryScopeFor(value: string): LibraryScope | undefined {
  return value === 'collection' || value === 'wantlist' ? value : undefined
}

function InfoIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
      <circle cx="8" cy="8" r="6.25" />
      <path d="M8 7.25v4" strokeLinecap="round" />
      <path d="M8 4.75h.01" strokeLinecap="round" />
    </svg>
  )
}

function BookmarkIcon({ filled }: { filled: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill={filled ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.5">
      <path d="M4 2h8a1 1 0 0 1 1 1v11l-5-3-5 3V3a1 1 0 0 1 1-1Z" strokeLinejoin="round" />
    </svg>
  )
}

// A judgment's justification, on demand. It replaces the native `title`
// tooltip the reason used to ride in, which announced nothing on the row,
// could not be reached on touch at all, and contended with the listing-title
// tooltip for the one slot both wanted.
//
// A popover rather than a modal: this is a glance at one sentence, so it opens
// and closes on the same icon, dims nothing, and needs no Close button.
// Opening moves no focus. It is a disclosure, not a dialog -- the icon owns
// the relationship through aria-expanded/aria-controls, and describes itself
// by the panel so a screen reader on the icon hears the reason. The panel
// itself is in the tab order for one reason only: a reason long enough to
// clip has to be scrollable by keyboard, which Safari will not do for a
// container it cannot focus.
// `env()` cannot be read from script, and a custom property holding one comes
// back unresolved, so the value is taken off a throwaway element that has the
// insets as real padding. Created and removed per call: this runs when the
// popover opens or the viewport resizes, not per frame, and a probe left in
// the document is a thing to explain later. Everything without safe areas --
// every desktop, and jsdom -- reports zero.
function safeAreaInsets(): Insets {
  const probe = document.createElement('div')
  probe.style.cssText = 'position:fixed;top:0;left:0;visibility:hidden;pointer-events:none;'
    + 'padding:env(safe-area-inset-top) env(safe-area-inset-right)'
    + ' env(safe-area-inset-bottom) env(safe-area-inset-left)'
  document.body.appendChild(probe)
  const style = getComputedStyle(probe)
  const insets = {
    top: parseFloat(style.paddingTop) || 0,
    right: parseFloat(style.paddingRight) || 0,
    bottom: parseFloat(style.paddingBottom) || 0,
    left: parseFloat(style.paddingLeft) || 0,
  }
  probe.remove()
  return insets
}

function ReasonPopover({ item, anchor, onClose }: { item: StockItem; anchor: HTMLElement; onClose: () => void }) {
  const panelRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<Placement | null>(null)

  // Measured after render and before paint, so the panel never shows at the
  // origin first. Its own width and height are inputs to the placement, which
  // is why this cannot be a static class.
  useLayoutEffect(() => {
    function place() {
      const panel = panelRef.current
      if (!panel) return
      // A view-mode or breakpoint switch mounts this popover afresh against
      // the anchor from the tree it replaced, which the same commit detaches.
      // The parent's own check ran a render too early to see that, so closing
      // here is what ends it -- without this the panel would sit hidden and
      // the new icon would keep claiming to be expanded until some unrelated
      // render came along.
      if (!anchor.isConnected) {
        onClose()
        return
      }
      const viewport = {
        width: window.innerWidth,
        height: window.innerHeight,
        insets: safeAreaInsets(),
      }
      const rect = anchor.getBoundingClientRect()
      // A refetch can move the row without any scroll -- a sync that inserts
      // rows above it in the current sort -- and this effect re-runs on the
      // new item. Placing against an anchor that has left the screen would
      // clamp the panel to an edge beside rows it has nothing to do with. The
      // claim here is only "on screen at all", which geometry can answer;
      // whether it is *visible* is what a scroll now dismisses rather than
      // computes. Strictly outside, so the all-zero rect an unlaid-out
      // element reports is not read as gone.
      if (rect.bottom < 0 || rect.top > viewport.height || rect.right < 0 || rect.left > viewport.width) {
        onClose()
        return
      }
      // The size the panel wants, which is not the size it currently has: the
      // last placement capped it, and measuring that back would keep it there
      // -- a panel opened on a narrow screen would never widen again when the
      // screen did. So the caps come off and the placement decides them
      // afresh. Written and read inside a layout effect, so nothing uncapped
      // is painted.
      panel.style.maxWidth = ''
      panel.style.maxHeight = ''
      const natural = panel.getBoundingClientRect().width
      // Then the width goes back on before the height is read, because the
      // height depends on it: the text reflows to whatever width the screen
      // leaves, and a height measured at the wider layout comes out short --
      // clipping the reason into a scrollbar with room to spare below it.
      panel.style.maxWidth = `${Math.min(natural, reasonPopoverMaxWidth(viewport))}px`
      const box = panel.getBoundingClientRect()
      const placement = placeReasonPopover(rect, {
        width: natural,
        // scrollHeight is the content's own height whatever cap is applied,
        // and the difference between the box and the client area is the
        // border it leaves out. Both read at the capped width, so they are
        // measurements of the same panel.
        height: panel.scrollHeight + (box.height - panel.clientHeight),
      }, viewport)
      // Put them back here rather than leaving it to the render setPos
      // schedules: the clear above went behind React, which will not re-write
      // a style value it already believes is applied -- so a placement that
      // returns what it returned last time would leave the panel uncapped.
      panel.style.maxWidth = `${placement.maxWidth}px`
      panel.style.maxHeight = `${placement.maxHeight}px`
      setPos(placement)
    }
    place()
    // A scroll dismisses it rather than moving it. Following the row would
    // mean deciding, on every scroll, whether the row is still *visible* --
    // and the row can be hidden while it is still in the viewport (scrolled
    // out of the table's own overflow container, or under the table's sticky
    // header), leaving a panel that usually names no record sitting beside
    // rows it has nothing to do with. Dismissing is both the simpler rule and the
    // one that matches a glance: you moved on. A scroll inside the panel is
    // the opposite -- it is how a long reason is read -- so it stays.
    // Capturing, since the containers that scroll do not bubble it.
    function onScroll(e: Event) {
      if (panelRef.current?.contains(e.target as Node)) return
      onClose()
    }
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', place)
    }
  }, [anchor, item, onClose])

  // Focus is handed back on unmount rather than in any one dismissal path:
  // Escape, the icon's second click, a press outside and a refetch all remove
  // the panel, and only the first of those would otherwise restore it. The
  // flag rather than a live activeElement read because focus has usually moved
  // on by the time the cleanup runs -- and where it moved to the icon by
  // itself (every browser but Safari, on the click path) the blur clears it,
  // so this never steals focus back from somewhere it belongs.
  const hadFocus = useRef(false)
  useEffect(() => () => {
    // preventScroll, because one of the ways this closes is the user
    // scrolling: focusing an anchor they have just scrolled away from would
    // have the browser scroll it back and undo them.
    if (hadFocus.current && anchor.isConnected) anchor.focus({ preventScroll: true })
  }, [anchor])

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== 'Escape') return
      onClose()
    }
    // Pointer-down, and never on the anchor: a press on the icon is the
    // toggle's own second click, and closing here first would leave the click
    // that follows to reopen what it was meant to close. Touch as well as
    // mouse, matching StockFilter -- a tap emits mousedown only as a
    // compatibility event, and a touch scroll emits none at all.
    function onPointerDown(e: MouseEvent | TouchEvent) {
      const target = e.target as Node
      if (panelRef.current?.contains(target) || anchor.contains(target)) return
      onClose()
    }
    // Focus landing outside as well, because a keyboard never presses: Enter
    // on a button emits `click` with no `mousedown` before it, so activating
    // one of App's nav tabs that way left this open. That matters more than
    // it sounds -- App parks the whole Store view under `hidden` rather than
    // unmounting it, so a popover that survives the switch goes on measuring
    // an anchor with no layout box and writes those zeros back as its own
    // size. Focus has to reach the tab before it can be activated, so this
    // catches it first. The same exemptions: focus moving into the panel is
    // how a long reason is scrolled, and moving to the icon is the toggle's
    // own business.
    function onFocusIn(e: FocusEvent) {
      const target = e.target as Node
      if (panelRef.current?.contains(target) || anchor.contains(target)) return
      onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('touchstart', onPointerDown)
    document.addEventListener('focusin', onFocusIn)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('touchstart', onPointerDown)
      document.removeEventListener('focusin', onFocusIn)
    }
  }, [anchor, onClose])

  return (
    <div
      ref={panelRef}
      id={REASON_PANEL_ID}
      onFocus={() => { hadFocus.current = true }}
      onBlur={() => { hadFocus.current = false }}
      // `note` rather than `tooltip`: an ARIA tooltip is a non-focusable
      // description shown on hover or focus, and this is a click-controlled
      // panel that deliberately takes a tab stop -- a focusable tooltip is a
      // pattern assistive tech has no good reading of. A note is what this
      // is: text ancillary to the row it hangs off.
      role="note"
      // Deliberately unnamed: this panel is the icon's aria-describedby
      // target, and an aria-label here would win the text-alternative
      // computation outright -- the icon would describe itself as
      // "Recommendation details" instead of reading out the justification,
      // which is the entire point of the relationship.
      // Focusable because the reason is free text and can outrun the panel:
      // Chrome and Firefox hand a scroll container to the keyboard on their
      // own, Safari does not, and the clipped tail has to be reachable
      // somehow. Rendered next to its icon so Tab reaches it from there.
      tabIndex={0}
      style={{
        top: pos?.top ?? 0,
        left: pos?.left ?? 0,
        maxHeight: pos?.maxHeight,
        maxWidth: pos?.maxWidth,
        visibility: pos ? 'visible' : 'hidden',
      }}
      className="fixed z-50 w-64 overflow-y-auto rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 shadow-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/80"
    >
      {/* A reason only exists on a judged item, so the polarity is never
          unknown here -- and it has to be said, since an item can be judged
          against and still carry a note explaining why. */}
      <p className="text-xs font-medium text-gray-400">
        {item.recommended ? 'Recommended' : 'Not recommended'}
      </p>
      {/* Usually the row this is pinned to says which record it is about, and
          repeating that is noise the modal could afford and a glance cannot.
          Not on a row showing a source's own name for what it matched: a
          judgment is made against an item_key, so there the reason would read
          as being about a pressing the judge never saw. gray-400 rather than
          the gray-500 of the app's other secondary text -- on gray-900 that
          is ~3.7:1, and this is the line that says which record. */}
      {namesAnotherPressing(item) && (
        <p className="text-xs text-gray-400 break-words">{item.artist} — {item.title}</p>
      )}
      {/* break-words because a reason is arbitrary imported text -- the CSV
          import strips it and stores it, with no bound on length or on how
          long a single token may be. One unbroken URL would otherwise run
          past a panel the placement has just fitted to the safe screen. */}
      <p className="mt-1 text-sm text-gray-200 break-words">{item.reason}</p>
    </div>
  )
}

function StockBrowser({
  recommendedAvailable = false, hiddenCrawlerIds = NO_HIDDEN_CRAWLER_IDS,
  crawlers = NO_CRAWLERS, onHiddenCrawlerIdsChange = NOOP_HIDDEN_CRAWLER_IDS_CHANGE,
  hiddenCrawlerIdsLoaded = true, syncGeneration, inventoryGeneration, judgmentGeneration,
  libraryGeneration, isAdmin = false, hasPriceField = true,
}: Props) {
  const isMobile = useIsMobile()
  const [items, setItems] = useState<StockItem[]>([])
  // Rows in the paginated set -- what pages divide by. A Cost sort in list
  // view flattens each item's comparison rows into one ordering, so a page
  // there holds PER_PAGE rows rather than PER_PAGE items; every other request
  // pages by item, where this just matches the response's `total`. Nothing
  // here renders that item count: the Stats panel surfaces it.
  const [rowTotal, setRowTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [selectedArtist, setSelectedArtist] = useState('')
  const [artists, setArtists] = useState<string[]>([])
  const [sort, setSort] = useState<StockSortField>('artist')
  const [order, setOrder] = useState<SortOrder>('asc')
  const [filter, setFilter] = useState<string>(() => {
    const allowed: readonly string[] = STORE_FILTERS
    const stored = localStorage.getItem('stockFilter_store')
    return stored && allowed.includes(stored) ? stored : 'all'
  })
  const [viewMode, setViewMode] = useState<'list' | 'tiles'>(
    () => (localStorage.getItem('collectionViewMode_store') === 'tiles' ? 'tiles' : 'list')
  )
  // Only the lowest-priced store's row for each record -- rows at the floor,
  // so a tie keeps both and each currency keeps its own (see
  // db._cheapest_clause). Stacks on every filter, Collection and Wantlist
  // included: under those it reads as "the cheapest place to buy a record I
  // follow", and unticking it is one click away.
  const [cheapest, setCheapest] = useState(() => localStorage.getItem('stockCheapest') === 'true')
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
  // What the open popover shows and what it measures itself against: the item
  // whose justification is on screen, and the icon it is pinned to. Null when
  // nothing is open. The popover renders beside that icon, so a refetch that
  // drops the row takes the panel with it -- which is why this state has to be
  // cleared from here rather than by the popover itself.
  const [reason, setReason] = useState<{ item: StockItem; anchor: HTMLElement } | null>(null)
  const PER_PAGE = 250
  const tableScrollRef = useRef<HTMLDivElement>(null)
  // A collection sync only moves rows under a filter that reads
  // library_items -- Collection and Wantlist directly, Overlapped through the
  // collected-artist clause, Recommended through its not-owned gate (see
  // db._stock_filter_sql). Under All and Saved the tick is pinned so it cannot
  // cause a refetch.
  const libraryTick = LIBRARY_DEPENDENT_FILTERS.has(filter) ? (libraryGeneration ?? 0) : 0

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

  // A refetch that drops the row unmounts its popover, which cannot then clear
  // this state itself -- and a row that came back would find it still set and
  // reopen unbidden. Adjusted during render, like the two resets above, since
  // the render that drops the row changes none of this component's own inputs
  // and so would not re-run a dependency-listed effect. Covers the view-mode
  // switch too: there the popover stays mounted, but the node it was measured
  // against does not.
  if (reason && !reason.anchor.isConnected) setReason(null)

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
      libraryScope: libraryScopeFor(filter),
      recommended: filter === 'recommended',
      saved: filter === 'saved',
      overlapped: filter === 'overlapped',
      hiddenCrawlerIds,
      cheapest,
      // Tiles render own rows only, so asking for comparison rows there would
      // spend a whole page of the flattened Cost ordering on rows the grid
      // then drops -- leaving it near-empty. Grouped sorts are unaffected
      // either way; this just stops fetching what tiles never show.
      includeComparisons: viewMode === 'list',
    })
    if (!isLatest()) return
    setItems(result.items)
    setRowTotal(result.row_total)
    setHasLoaded(true)
  }, [search, selectedArtist, sort, order, page, filter, hiddenCrawlerIds, hiddenCrawlerIdsLoaded, viewMode, cheapest])

  // syncGeneration ticks on every stock_sync_progress/stock_sync_complete SSE
  // event so the Store tab repaints as crawlers add items, same as
  // RecordBrowser's syncGeneration does for collection sync. Kept in this
  // same effect as `load` (rather than a second `if (syncGeneration) load()`
  // effect) so a syncGeneration tick and an unrelated load-identity change
  // (search/sort/filter/page/...) can never both fire and double-call load().
  useEffect(() => {
    let latest = true
    load(() => latest)
    return () => { latest = false }
  }, [load, syncGeneration, retryTick, libraryTick])
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
      libraryScope: libraryScopeFor(filter),
      recommended: filter === 'recommended',
      saved: filter === 'saved',
      overlapped: filter === 'overlapped',
      hiddenCrawlerIds,
    }).then((list) => { if (latest) setArtists(list) })
    return () => { latest = false }
  }, [filter, hiddenCrawlerIds, syncGeneration, retryTick, hiddenCrawlerIdsLoaded, libraryTick])
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
  useEffect(() => { localStorage.setItem('collectionViewMode_store', viewMode) }, [viewMode])
  useEffect(() => { localStorage.setItem('stockFilter_store', filter) }, [filter])
  useEffect(() => { localStorage.setItem('stockCheapest', String(cheapest)) }, [cheapest])
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
    // wantlist row has no paid price -- so anywhere but Collection it silently
    // degrades to artist order. Resetting the sort keeps the visible sort
    // indicator honest instead of leaving state claiming an order the rows
    // aren't in; the Price column itself only renders under Collection.
    if (value !== 'collection' && sort === 'discogs_price') {
      setSort('artist')
      setOrder('asc')
    }
  }

  const closeReason = useCallback(() => setReason(null), [])



  // The icon is the whole control: a second click on the one already showing
  // closes it, and a click on another row's swaps to that one. Compared by
  // item id rather than by node, so a refetch between the two clicks cannot
  // turn the closing click into a reopening one.
  function toggleReason(e: ReactMouseEvent<HTMLButtonElement>, item: StockItem) {
    const anchor = e.currentTarget
    setReason((open) => (open?.item.id === item.id ? null : { item, anchor }))
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
  // The discogs price is what the user paid, which only a collection row has,
  // so the column and its sort exist only under the Collection filter -- and
  // only while the user has any price data at all.
  const showPrice = filter === 'collection' && hasPriceField
  const colCount = showPrice ? 8 : 7
  const emptyMessage =
    filter === 'recommended' ? 'Nothing recommended is in stock right now.'
    : filter === 'saved' ? "You haven't saved anything yet."
    : filter === 'overlapped' ? 'Nothing by an artist in your collection is in stock right now.'
    : filter === 'collection' ? 'Nothing in your collection is in stock right now.'
    : filter === 'wantlist' ? 'Nothing on your wantlist is in stock right now.'
    : isAdmin ? 'No in-stock items yet. Click Refresh under Store Management in Settings.'
    : 'No in-stock items yet. Check back after the next store sync.'

  // Mirrors the column headers below, gated the same way: the discogs price is
  // only a column, and only sortable, where the table shows one.
  const sortOptions: SortOption<StockSortField>[] = [
    { field: 'artist', label: 'Artist' },
    { field: 'title', label: 'Title' },
    { field: 'format', label: 'Format' },
    ...(showPrice ? [{ field: 'discogs_price', label: 'Price' } as SortOption<StockSortField>] : []),
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
          {/* No item count on the search line: the Stats button beside the
              toolbar already surfaces the total. */}
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
              <StockStats
                  search={search || undefined}
                  artist={selectedArtist || undefined}
                  libraryScope={libraryScopeFor(filter)}
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
                    + libraryTick
                    + retryTick
                  }
                  disabled={!hiddenCrawlerIdsLoaded}
                />
              <StockFilter
                filter={filter}
                onFilterChange={changeFilter}
                recommendedAvailable={recommendedAvailable}
                cheapest={cheapest}
                onCheapestChange={(value) => { setCheapest(value); setPage(1) }}
              />
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
                  // The card is a plain wrapper, with the listing link and the
                  // action group as siblings inside it. The buttons used to sit
                  // within the link, held harmless by an e.preventDefault() --
                  // but a control nested in a control is invalid whatever the
                  // click does, and assistive tech is not obliged to expose the
                  // inner one, which for an info button that exists to replace
                  // an unreachable tooltip defeats the point.
                  <div key={item.id} className="group relative">
                    <a href={item.url} target="_blank" rel="noreferrer" className="block">
                      {item.cover_image_url ? (
                        <img
                          src={item.cover_image_url}
                          alt={item.title}
                          className="w-full aspect-square object-cover rounded"
                        />
                      ) : (
                        <div className="w-full aspect-square bg-gray-800 rounded" />
                      )}
                      <div className="mt-1.5 text-sm text-gray-200 truncate group-hover:text-white">{item.artist}</div>
                      <div className="text-xs text-gray-400 truncate" title={titleTooltip(item)}>{displayTitle(item)}</div>
                    </a>
                    <div className="absolute top-1 right-1 flex items-center gap-1">
                      {item.reason && (
                        <>
                          <button
                            onClick={(e) => toggleReason(e, item)}
                            title={REASON_BUTTON_TITLE}
                            aria-expanded={reason?.item.id === item.id}
                            aria-describedby={reason?.item.id === item.id ? REASON_PANEL_ID : undefined}
                            aria-controls={reason?.item.id === item.id ? REASON_PANEL_ID : undefined}
                            className="flex h-11 w-11 items-center justify-center rounded-full bg-gray-950/70 text-white hover:bg-gray-950 md:h-auto md:w-auto md:p-1"
                          >
                            <InfoIcon />
                          </button>
                          {reason?.item.id === item.id && (
                            <ReasonPopover item={item} anchor={reason.anchor} onClose={closeReason} />
                          )}
                        </>
                      )}
                      <button
                        onClick={() => toggleSaved(item)}
                        title={item.saved ? 'Remove from saved' : 'Save for later'}
                        disabled={pendingSaves.has(item.item_key)}
                        className="flex h-11 w-11 items-center justify-center rounded-full bg-gray-950/70 text-white hover:bg-gray-950 disabled:opacity-40 md:h-auto md:w-auto md:p-1"
                      >
                        <BookmarkIcon filled={item.saved} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Card list -- see RecordBrowser for why cards rather than a
            side-scrolling table. The row's actions stay on the right: the cost
            link and the save button always, with the info button between them
            on a row whose item carries a judgment reason. */}
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
                  showPrice && item.discogs_price ? `Price ${item.discogs_price}` : null,
                ].filter(Boolean).join(' · ')
                return (
                  <li key={item.id} className="flex items-center gap-3 px-3 py-2 text-left">
                    {item.cover_image_url ? (
                      <img src={item.cover_image_url} alt="" className="w-14 h-14 shrink-0 object-cover rounded" />
                    ) : (
                      <div className="w-14 h-14 shrink-0 bg-gray-800 rounded" />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm text-gray-200">{item.artist}</div>
                      <div className="truncate text-sm text-gray-300" title={titleTooltip(item)}>{displayTitle(item)}</div>
                      {meta && <div className="truncate text-xs text-gray-500">{meta}</div>}
                      {/* The reason used to print here, as the stand-in for a
                          hover a touch device cannot perform. The info button
                          to the right is that stand-in now, and it carries the
                          verdict with it -- which this line never did, so an
                          imported rejection's note read as a recommendation. */}
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <a href={item.url} target="_blank" rel="noreferrer" className="px-2 py-3 text-sm font-medium text-green-400 hover:text-green-300">
                        {item.price != null ? formatPrice(item.price, item.currency) : 'View'}
                      </a>
                        {item.reason && (
                          <>
                            <button
                              onClick={(e) => toggleReason(e, item)}
                              title={REASON_BUTTON_TITLE}
                              aria-expanded={reason?.item.id === item.id}
                              aria-describedby={reason?.item.id === item.id ? REASON_PANEL_ID : undefined}
                              aria-controls={reason?.item.id === item.id ? REASON_PANEL_ID : undefined}
                              className={`w-11 h-11 flex items-center justify-center ${dismissButtonClass()}`}
                            >
                              <InfoIcon />
                            </button>
                            {reason?.item.id === item.id && (
                              <ReasonPopover item={item} anchor={reason.anchor} onClose={closeReason} />
                            )}
                          </>
                        )}
                        <button
                          onClick={() => toggleSaved(item)}
                          title={item.saved ? 'Remove from saved' : 'Save for later'}
                          disabled={pendingSaves.has(item.item_key)}
                          className={`w-11 h-11 flex items-center justify-center disabled:opacity-40 ${dismissButtonClass()}`}
                        >
                          <BookmarkIcon filled={item.saved} />
                        </button>
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
                {showPrice && (
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
                  <button type="button" onClick={() => toggleSort('source')} aria-label="Sort by source" className={`${sortButtonClass} text-center`}>
                    Source {sort === 'source' ? (order === 'asc' ? '↑' : '↓') : ''}
                  </button>
                </th>
                <th className="w-20 px-3 py-2"></th>
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
                  <td className="px-3 py-2 text-right text-gray-200">{item.artist}</td>
                  <td className="px-3 py-2 text-left text-gray-300" title={titleTooltip(item)}>{displayTitle(item)}</td>
                  <td className="px-3 py-2 text-gray-400">{item.format ?? '—'}</td>
                  {showPrice && (
                    <td className="px-3 py-2 text-gray-400">{item.discogs_price ?? '—'}</td>
                  )}
                  <td className="px-3 py-2">
                    <a href={item.url} target="_blank" rel="noreferrer" className="text-green-400 hover:text-green-300 font-medium">
                      {item.price != null ? formatPrice(item.price, item.currency) : 'View'}
                    </a>
                  </td>
                  <td className="px-3 py-2 text-gray-400">{item.source}</td>
                    <td className="px-3 py-2">
                      <div className="flex items-center justify-end gap-1">
                        {item.reason && (
                          <>
                            <button
                              onClick={(e) => toggleReason(e, item)}
                              title={REASON_BUTTON_TITLE}
                              aria-expanded={reason?.item.id === item.id}
                              aria-describedby={reason?.item.id === item.id ? REASON_PANEL_ID : undefined}
                              aria-controls={reason?.item.id === item.id ? REASON_PANEL_ID : undefined}
                              className={`p-1 ${dismissButtonClass()}`}
                            >
                              <InfoIcon />
                            </button>
                            {reason?.item.id === item.id && (
                              <ReasonPopover item={item} anchor={reason.anchor} onClose={closeReason} />
                            )}
                          </>
                        )}
                        <button
                          onClick={() => toggleSaved(item)}
                          title={item.saved ? 'Remove from saved' : 'Save for later'}
                          disabled={pendingSaves.has(item.item_key)}
                          className={`p-1 disabled:opacity-40 ${dismissButtonClass()}`}
                        >
                          <BookmarkIcon filled={item.saved} />
                        </button>
                      </div>
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
