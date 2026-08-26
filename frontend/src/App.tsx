import { useState, useEffect, useCallback, useRef } from 'react'
import RecordBrowser from './views/RecordBrowser'
import StockBrowser from './views/StockBrowser'
import Settings from './views/Settings'
import Account from './views/Account'
import LogViewer from './views/LogViewer'
import QueueView from './views/QueueView'
import LoginScreen from './views/LoginScreen'
import InviteCodeScreen from './views/InviteCodeScreen'
import BackendDownScreen from './views/BackendDownScreen'
import Avatar from './components/Avatar'
import { navButtonClass, primaryButtonClass, secondaryButtonClass, dismissButtonClass } from './styles/buttons'
import { refreshCollection, getCollectionStatus, openCrawlStream, getCrawlStatus, postCrawlStart, postStockSyncStart, postJudgmentStart, clearJudgments, exportRecommendationsCsv, importRecommendationsCsv, getCrawlers, getUserSettings, getUserHiddenCrawlers, postUserHiddenCrawlers, getJudgmentStatus, getPriceStatus, checkHealth, getAuthStatus, setUnauthorizedHandler, hasAvatar } from './api/client'
import type { StockSyncStartResult } from './api/client'
import type { CrawlEvent, CrawlStatus, CollectionStatus, Crawler, AuthStatus } from './api/types'

type View = 'collection' | 'wantlist' | 'store' | 'track' | 'settings' | 'logs' | 'queue' | 'account'

// SSE reconnects (including on browser refresh) replay every buffered event from
// crawl_manager._recent, so a banner's dismissal has to survive across that replay.
// Each broadcast event carries a monotonic `id`; we persist the id of the last-dismissed
// event and only show a banner when the current event's id is newer than that.
const DISMISSED_SYNC_KEY = 'discogs-browser.dismissedSyncEventId'
const DISMISSED_CRAWL_KEY = 'discogs-browser.dismissedCrawlEventId'
const VIEW_AS_USER_KEY = 'discogs-browser.viewAsUser'

// How long an optimistic stock-sync claim may hold a Refresh button before it
// releases on its own. An accepted start normally hands over to
// stock_sync_started within a second, but nothing guarantees that event ever
// arrives: the SSE stream can break and reconnect across an entire short sync,
// and routers/crawl.py's _events_to_replay returns nothing once no job is
// active -- so a sync that both started and finished inside the gap replays
// neither event. Unbounded, the claim would leave every Store Refresh button
// disabled and spinning until a page reload. Releasing is self-correcting
// either way: a late stock_sync_started takes the button straight back, and a
// click during a sync the UI has lost track of is rejected by the server with
// the "already running" message rather than starting anything twice.
const STOCK_SYNC_CLAIM_TIMEOUT_MS = 20_000

function formatElapsed(seconds: number | null): string {
  if (seconds === null) return 'unknown'
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

// A stock sync is one shared job under one advisory lock, so a Refresh clicked
// while another source is mid-crawl is rejected outright. That came back as a
// started=false nobody rendered, so the click looked like it had done nothing --
// on a catalog source that takes over an hour, indistinguishable from a hang.
function reportStockSyncRejection(
  result: StockSyncStartResult,
  setStatus: (message: string, id?: number | null) => void,
) {
  if (result.started) return
  if (result.on_another_instance) {
    setStatus('In-stock sync already running on another instance. Try again once it finishes.')
    return
  }
  const on = result.source
    ? `${result.source} (${formatElapsed(result.source_elapsed_seconds)} so far)`
    : 'starting up'
  setStatus(
    `In-stock sync already running — ${on}, ${formatElapsed(result.elapsed_seconds)} in total. Try again once it finishes.`,
  )
}

export default function App() {
  const [view, setView] = useState<View>('collection')
  const [crawling, setCrawling] = useState(false)
  const [crawlBannerId, setCrawlBannerId] = useState(0)
  const [dismissedCrawlId, setDismissedCrawlId] = useState(() => Number(localStorage.getItem(DISMISSED_CRAWL_KEY) ?? 0))
  const [crawlCurrent, setCrawlCurrent] = useState<CrawlEvent | null>(null)
  const [crawlCount, setCrawlCount] = useState(0)
  const [crawlTotal, setCrawlTotal] = useState(0)
  const [checkpointStatus, setCheckpointStatus] = useState<CrawlStatus | null>(null)

  const [collectionStatus, setCollectionStatus] = useState<CollectionStatus | null>(null)
  const [crawlers, setCrawlers] = useState<Crawler[]>([])
  const [hiddenCrawlerIds, setHiddenCrawlerIds] = useState<number[]>([])
  const [hiddenCrawlerIdsLoaded, setHiddenCrawlerIdsLoaded] = useState(false)
  const hiddenCrawlerIdsSaveChain = useRef<Promise<void>>(Promise.resolve())
  const latestHiddenCrawlerIdsSaveSeq = useRef(0)
  const [avatarVersion, setAvatarVersion] = useState(0)
  const [hasAnthropicKey, setHasAnthropicKey] = useState(false)
  const [hasJudgedItems, setHasJudgedItems] = useState(false)
  const [hasPriceData, setHasPriceData] = useState(false)
  const latestPriceStatusSeq = useRef(0)
  // Same race, same fix, for the two start requests. Neither has a bounded
  // response time -- POST /stock/sync/start opens a fresh psycopg connection
  // before it answers -- and the claim timeout below can re-enable the button
  // while one is still in flight. Without these, a first request rejecting
  // after a second click would clear the *newer* claim and overwrite its
  // status with the older request's result.
  const latestStockSyncStartSeq = useRef(0)
  const latestPriceRefreshSeq = useRef(0)
  const latestHasJudgedItemsSeq = useRef(0)
  const [serverReady, setServerReady] = useState(false)
  const [backendUp, setBackendUp] = useState<boolean | null>(null)
  const [authRevalidating, setAuthRevalidating] = useState(false)
  const [syncMessage, setSyncMessage] = useState<string | null>(null)
  const [syncMessageId, setSyncMessageId] = useState<number | null>(null)
  const [dismissedSyncId, setDismissedSyncId] = useState(() => Number(localStorage.getItem(DISMISSED_SYNC_KEY) ?? 0))
  const [syncing, setSyncing] = useState(false)
  const [syncGeneration, setSyncGeneration] = useState(0)
  const [stockSyncGeneration, setStockSyncGeneration] = useState(0)
  const [stockSyncTarget, setStockSyncTarget] = useState<number | 'all' | null>(null)
  // Optimistic twin of stockSyncTarget, set the instant a Refresh is clicked.
  // stockSyncTarget can't do this job on its own: it's only set once the
  // stock_sync_started event arrives, and POST /stock/sync/start has to open a
  // fresh Postgres connection and take an advisory lock before it even
  // returns. That gap left every Refresh button inert for a beat after the
  // click -- the same "did that do anything?" the rejected-start message was
  // added to fix, in the accepted-start case it never covered.
  const [stockSyncStarting, setStockSyncStarting] = useState<number | 'all' | null>(null)
  // Prices have no equivalent of stockSyncTarget to hand over to, and never
  // will: POST /crawl/start only enqueues, and the worker pool that later
  // picks the work up broadcasts no lifecycle event at all (started/complete/
  // stopped went with the crawl-queue refactor). Nothing is coming, so this
  // covers the request itself and the count in its reply is the confirmation.
  const [priceRefreshStarting, setPriceRefreshStarting] = useState(false)
  // The message whose presence on screen means "still waiting on this". The
  // banner's spinner is derived from it rather than from "is anything pending
  // anywhere", because the two drift apart: a stock sync finishing while a
  // price request was still in flight left its *completion* message spinning
  // with no Dismiss button. Comparing text means a message that has since been
  // replaced simply stops matching, with nothing to keep in sync by hand.
  const [busyStatusMessage, setBusyStatusMessage] = useState<string | null>(null)
  const [authState, setAuthState] = useState<AuthStatus | null>(null)
  const [viewAsUser, setViewAsUser] = useState(() => localStorage.getItem(VIEW_AS_USER_KEY) === 'true')
  const [signupToken, setSignupToken] = useState<string | null>(() => {
    const params = new URLSearchParams(window.location.search)
    return params.get('signup_pending')
  })

  // eventId is null for locally-generated messages (button-click failures) that never
  // survive a refresh and so never need replay suppression; those always show.
  const setSyncStatus = useCallback((message: string, eventId: number | null = null) => {
    setSyncMessage(message)
    setSyncMessageId(eventId)
  }, [])

  // Releasing the button is only half of it: the "Starting…" message it was
  // clicked with has to go too, or the banner ends up showing a Dismiss button
  // beside "Starting…", which reads as finished -- exactly the state syncBusy
  // exists to prevent. Replaced rather than blanked, because the user is owed
  // an account of a refresh the app has lost track of, and only a reload can
  // settle whether it is still running (an SSE reconnect replays a job that
  // still is). Guarded on the message still being the one this claim set, so a
  // real progress message that arrived in the meantime is never clobbered.
  const stockSyncClaimNotice = useRef<{ shown: string; lost: string } | null>(null)

  useEffect(() => {
    if (stockSyncStarting === null) return
    const timer = setTimeout(() => {
      setStockSyncStarting(null)
      const notice = stockSyncClaimNotice.current
      if (notice) setSyncMessage((m) => (m === notice.shown ? notice.lost : m))
    }, STOCK_SYNC_CLAIM_TIMEOUT_MS)
    return () => clearTimeout(timer)
  }, [stockSyncStarting])

  const updateHiddenCrawlerIds = useCallback((ids: number[]) => {
    setHiddenCrawlerIds(ids)
    const seq = ++latestHiddenCrawlerIdsSaveSeq.current
    hiddenCrawlerIdsSaveChain.current = hiddenCrawlerIdsSaveChain.current.then(async () => {
      try {
        await postUserHiddenCrawlers(ids)
      } catch {
        if (seq !== latestHiddenCrawlerIdsSaveSeq.current) return
        setSyncStatus('Could not save your source filter — try again.')
      }
    })
  }, [setSyncStatus])

  // Bootstrap and the post-sync refresh below can both have a getPriceStatus()
  // request in flight at once; without a sequence guard, a slow-arriving
  // bootstrap response can land after the newer post-sync one and overwrite it
  // with stale data.
  const fetchPriceStatus = useCallback(() => {
    const seq = ++latestPriceStatusSeq.current
    getPriceStatus().then((s) => {
      if (seq !== latestPriceStatusSeq.current) return
      setHasPriceData(s.any_price_paid)
    }).catch(() => {})
  }, [])

  // Same race, same fix, for hasJudgedItems: the bootstrap fetch below and
  // handleImportRecommendations's post-import refresh can both have a
  // getJudgmentStatus() request in flight, and the judgment SSE handlers and
  // handleClearRecommendations's explicit write are two more sources that
  // can land in between. Every writer shares this one counter -- the SSE
  // handlers and the clear handler bump it before writing directly (they
  // already know the answer, no fetch needed), so a slower fetch that was
  // already in flight loses the race and its stale result is discarded.
  const refreshJudgmentStatus = useCallback(() => {
    const seq = ++latestHasJudgedItemsSeq.current
    getJudgmentStatus().then((s) => {
      if (seq !== latestHasJudgedItemsSeq.current) return
      setHasJudgedItems(s.any_judged)
    }).catch(() => {})
  }, [])

  // Continuous, unconditional health poll -- drives `backendUp`, which gates
  // BackendDownScreen for both "backend not up yet" and "backend went down
  // mid-session" the same way, since the frontend can't tell those apart.
  // Asymmetric debounce: 2 consecutive failures before flipping down (avoids
  // flicker from one dropped request), 1 success flips back up immediately.
  useEffect(() => {
    let cancelled = false
    let consecutiveFailures = 0
    let wasUp = false
    async function poll() {
      while (!cancelled) {
        const ok = await checkHealth()
        if (!cancelled) {
          if (ok) {
            consecutiveFailures = 0
            if (!wasUp) {
              // Set together in the same commit as setBackendUp(true), and
              // only on the down/null -> up transition (not every routine
              // tick while already up) -- setting it separately, from the
              // auth-status effect that only fires afterward (once it
              // observes backendUp change), would leave a render in between
              // where backendUp is already true but authRevalidating is
              // still stale-false, briefly clearing the overlay/inert state
              // before revalidation has even started.
              setAuthRevalidating(true)
            }
            wasUp = true
            setBackendUp(true)
          } else {
            consecutiveFailures += 1
            if (consecutiveFailures >= 2) {
              wasUp = false
              setBackendUp(false)
            }
          }
        }
        await new Promise(r => setTimeout(r, 2000))
      }
    }
    poll()
    return () => { cancelled = true }
  }, [])

  // One-time bootstrap once both auth and the backend are confirmed ready.
  useEffect(() => {
    if (authState?.state !== 'authenticated') return
    if (!backendUp || serverReady) return
    setServerReady(true)
    getCrawlers().then(setCrawlers).catch(() => {})
    getUserHiddenCrawlers().then((ids) => {
      setHiddenCrawlerIds(ids)
      setHiddenCrawlerIdsLoaded(true)
    }).catch(() => {
      setSyncStatus('Could not load your source filter — reload the page to try again.')
    })
    getUserSettings().then((s) => {
      setHasAnthropicKey(Boolean(s.anthropic_api_key))
    }).catch(() => {})
    refreshJudgmentStatus()
    fetchPriceStatus()
    hasAvatar().then((exists) => setAvatarVersion(exists ? Date.now() : 0)).catch(() => {})
  }, [authState, backendUp, serverReady, setSyncStatus, fetchPriceStatus, refreshJudgmentStatus])

  // Persistent SSE connection — reconnects on error. Gated on authState only
  // (not backendUp) -- it reconnects through any backend outage on its own
  // 3s backoff, independent of the health-poll state machine.
  // Handles both user-triggered and scheduled crawls.
  useEffect(() => {
    if (authState?.state !== 'authenticated') return
    let source: EventSource | null = null
    let reconnectTimer: ReturnType<typeof setTimeout>
    let destroyed = false

    function handleEvent(e: MessageEvent) {
      const event: CrawlEvent = JSON.parse(e.data)
      if (event.status === 'ping') return
      if (event.status === 'sync_started') {
        setSyncing(true)
        setSyncStatus(event.scope === 'wishlist' ? 'Syncing wantlist…' : 'Syncing collection…', event.id ?? null)
        return
      }
      if (event.status === 'sync_page_fetched') {
        setSyncStatus(`Syncing collection… ${event.page_count} records (page ${event.page}/${event.total_pages})`, event.id ?? null)
        return
      }
      if (event.status === 'sync_progress') {
        setSyncStatus(`Syncing collection… ${event.synced} records (page ${event.page}/${event.total_pages})`, event.id ?? null)
        setSyncGeneration(g => g + 1)
        return
      }
      if (event.status === 'sync_complete') {
        setSyncing(false)
        if (event.scope === 'wishlist') {
          setSyncStatus(`Synced ${event.wishlist_synced} wantlist items for ${event.username}`, event.id ?? null)
        } else {
          const wantlistPart = event.wishlist_synced != null ? `, ${event.wishlist_synced} wantlist items` : ''
          setSyncStatus(`Synced ${event.synced} records for ${event.username}${wantlistPart}`, event.id ?? null)
          fetchPriceStatus()
        }
        setSyncGeneration(g => g + 1)
        return
      }
      if (event.status === 'sync_error') {
        setSyncing(false)
        setSyncStatus(`Sync failed: ${event.error}`, event.id ?? null)
        // Each page's writes (including price_paid) commit before the next page
        // starts, so a sync that fails partway through can still have changed
        // stored prices -- refetch regardless of which scope errored.
        fetchPriceStatus()
        return
      }
      if (event.status === 'plex_match_started') {
        setSyncStatus('Matching collection against Plex…', event.id ?? null)
        return
      }
      if (event.status === 'plex_match_progress') {
        setSyncStatus(`Matching collection against Plex… ${event.matched}/${event.total}`, event.id ?? null)
        return
      }
      if (event.status === 'plex_match_complete') {
        setSyncStatus(`Plex match complete — ${event.matched} matched`, event.id ?? null)
        return
      }
      if (event.status === 'plex_match_error') {
        setSyncStatus(`Plex match failed: ${event.error}`, event.id ?? null)
        return
      }
      if (event.status === 'stock_sync_started') {
        setSyncing(true)
        setStockSyncTarget(event.crawler_id ?? 'all')
        setStockSyncStarting(null)
        setSyncStatus('Syncing in-stock catalog…', event.id ?? null)
        return
      }
      if (event.status === 'stock_sync_source_started') {
        setSyncStatus(`Syncing in-stock catalog… ${event.source}`, event.id ?? null)
        return
      }
      if (event.status === 'stock_sync_page_fetched') {
        const products = event.page_count === 1 ? 'product' : 'products'
        setSyncStatus(
          `Syncing in-stock catalog… ${event.source} fetched page ${event.page}, ${event.page_count} ${products}`,
          event.id ?? null,
        )
        return
      }
      if (event.status === 'stock_sync_detail_progress') {
        // "detail pages", not "releases": Dark Descent's total counts the
        // variable products on a listing page that also carries simple ones,
        // so a release count would understate the page it names.
        const pages = event.total === 1 ? 'detail page' : 'detail pages'
        setSyncStatus(
          `Syncing in-stock catalog… ${event.source} ${event.label} — ${event.done}/${event.total} ${pages}`,
          event.id ?? null,
        )
        return
      }
      if (event.status === 'stock_sync_progress') {
        setSyncStatus(`Syncing in-stock catalog… ${event.synced} items (${event.source})`, event.id ?? null)
        setStockSyncGeneration(g => g + 1)
        return
      }
      if (event.status === 'stock_sync_complete') {
        setSyncing(false)
        setStockSyncTarget(null)
        setStockSyncStarting(null)
        setSyncStatus(`In-stock sync complete: ${event.synced} items`, event.id ?? null)
        setStockSyncGeneration(g => g + 1)
        return
      }
      if (event.status === 'stock_sync_error') {
        if (!event.source) {
          setSyncing(false)
          setStockSyncTarget(null)
          setStockSyncStarting(null)
        }
        setSyncStatus(`In-stock sync failed: ${event.error}`, event.id ?? null)
        return
      }
      if (event.status === 'stock_sync_aborted') {
        setSyncing(false)
        setStockSyncTarget(null)
        setStockSyncStarting(null)
        const sources = event.sources?.length ? ` (${event.sources.join(', ')})` : ''
        setSyncStatus(`In-stock sync stopped: ${event.error}${sources}`, event.id ?? null)
        return
      }
      if (event.status === 'stock_judgment_started') {
        setSyncing(true)
        setSyncStatus('Finding recommendations for Store items…', event.id ?? null)
        return
      }
      if (event.status === 'stock_judgment_progress') {
        if ((event.judged ?? 0) > 0) {
          latestHasJudgedItemsSeq.current++
          setHasJudgedItems(true)
        }
        setStockSyncGeneration(g => g + 1)
        setSyncStatus(`Finding recommendations for Store items… ${event.judged}/${event.total}`, event.id ?? null)
        return
      }
      if (event.status === 'stock_judgment_complete') {
        setSyncing(false)
        if ((event.judged ?? 0) > 0) {
          latestHasJudgedItemsSeq.current++
          setHasJudgedItems(true)
        }
        setStockSyncGeneration(g => g + 1)
        setSyncStatus(`Finished finding recommendations — ${event.judged} items checked`, event.id ?? null)
        return
      }
      if (event.status === 'stock_judgment_error') {
        setSyncing(false)
        setSyncStatus(`Finding recommendations failed: ${event.error}`, event.id ?? null)
        return
      }
      if (event.type === 'listing_changed') {
        setStockSyncGeneration(g => g + 1)
        return
      }
      if (event.status === 'started') {
        setCrawlTotal(event.total ?? 0)
        setCrawling(true)
        setCrawlBannerId(event.id ?? 0)
        setCrawlCount(0)
        setCrawlCurrent(null)
      } else if (event.status === 'complete' || event.status === 'stopped') {
        setCrawling(false)
        setCrawlCurrent(null)
      } else if (event.status === 'error' && !event.release) {
        setCrawling(false)
      } else if (event.release) {
        setCrawlCurrent(event)
        setCrawlCount((n) => n + 1)
      }
    }

    function connect() {
      if (destroyed) return
      source = openCrawlStream()
      source.onmessage = handleEvent
      source.onerror = () => {
        source?.close()
        if (!destroyed) reconnectTimer = setTimeout(connect, 3000)
      }
    }

    connect()
    return () => {
      destroyed = true
      source?.close()
      clearTimeout(reconnectTimer)
    }
  }, [authState, setSyncStatus, fetchPriceStatus])

  useEffect(() => {
    setUnauthorizedHandler(() => setAuthState({ state: 'unauthenticated' }))
  }, [])

  // Re-checked every time the backend transitions from down to up -- covers
  // both the first successful check and revalidating the session after an
  // outage. A stale authState from before an outage is harmless to render
  // in the meantime: pre-auth, the render guard still shows BackendDownScreen
  // until this fetch gets a chance to run; post-auth, authRevalidating keeps
  // the overlay/inert state active (see the bottom of this component) until
  // this fetch actually resolves, not just until backendUp flips true --
  // otherwise the frozen app would briefly un-freeze before its session is
  // reconfirmed. The `cancelled` guard discards a response from a request
  // superseded by a later down/up flap, so an older response can never
  // overwrite a newer one.
  useEffect(() => {
    if (!backendUp) return
    let cancelled = false
    setAuthRevalidating(true)
    getAuthStatus()
      .then((status) => { if (!cancelled) setAuthState(status) })
      .catch(() => { if (!cancelled) setAuthState({ state: 'unauthenticated' }) })
      .finally(() => { if (!cancelled) setAuthRevalidating(false) })
    return () => { cancelled = true }
  }, [backendUp])

  const startRefresh = useCallback(async (mode: 'all' | 'new') => {
    setCollectionStatus(null)
    try {
      await refreshCollection(mode)
    } catch (e: any) {
      setSyncStatus(`Sync failed: ${e.message}`)
    }
  }, [setSyncStatus])

  const handleRefresh = useCallback(async (mode?: 'all' | 'new') => {
    if (mode) {
      startRefresh(mode)
      return
    }
    try {
      const status = await getCollectionStatus()
      if (status.total > 0) {
        setCollectionStatus(status)
        return
      }
    } catch {
      // fall through to full refresh
    }
    startRefresh('all')
  }, [startRefresh])

  // Wantlist tab's refresh has nothing analogous to the "N records already
  // loaded, refresh new or all?" choice that collectionStatus's modal offers --
  // wantlists are small and always fully re-synced -- so this skips straight
  // to the sync, same as Settings' "Refresh Now" bypassing that modal.
  const handleRefreshWantlist = useCallback(async () => {
    try {
      await refreshCollection('all', 'wantlist')
    } catch (e: any) {
      setSyncStatus(`Sync failed: ${e.message}`)
    }
  }, [setSyncStatus])

  // POST /crawl/start only enqueues, and the shared worker pool broadcasts no
  // lifecycle event when it later picks the work up (the `started` event went
  // with the crawl-queue refactor). So the reply's own count is the only
  // confirmation that exists at click time. It is deliberately reported as
  // records *requested*, not queued: routers/crawl.py counts targets, while
  // db.enqueue_crawl_queue no-ops on a row that is already pending or
  // in_progress -- and "requested" is the more useful of the two anyway, since
  // an affected-row count would say "0" for a re-click mid-crawl whose records
  // are all queued and about to be crawled. This also replaces the alert()
  // this used to raise on failure, the one error path in the app that blocked
  // the page.
  const startCrawl = useCallback(async (releaseId?: string, mode?: 'all' | 'missing') => {
    const seq = ++latestPriceRefreshSeq.current
    setCheckpointStatus(null)
    setPriceRefreshStarting(true)
    const starting = releaseId
      ? 'Starting price refresh for this record…'
      : mode === 'missing'
        ? 'Starting price refresh for records with no price yet…'
        : 'Starting price refresh for every record…'
    setBusyStatusMessage(starting)
    setSyncStatus(starting)
    try {
      const { enqueued } = await postCrawlStart(mode ?? 'all', releaseId)
      if (seq !== latestPriceRefreshSeq.current) return
      setSyncStatus(enqueued === 0
        ? (mode === 'missing'
          ? 'Nothing to refresh — every record already has a price.'
          : 'Nothing to refresh — no records matched.')
        : `Price refresh requested for ${enqueued} ${enqueued === 1 ? 'record' : 'records'}.`)
    } catch (e: any) {
      if (seq !== latestPriceRefreshSeq.current) return
      setSyncStatus(`Price refresh failed to start: ${e.message}`)
    } finally {
      if (seq === latestPriceRefreshSeq.current) setPriceRefreshStarting(false)
    }
  }, [setSyncStatus])

  const handleFindPrices = useCallback(async (releaseId?: string, mode?: 'all' | 'missing') => {
    if (releaseId) {
      startCrawl(releaseId, undefined)
      return
    }
    if (mode) {
      startCrawl(undefined, mode)
      return
    }
    try {
      const status = await getCrawlStatus()
      if (status.total > 0 && status.missing > 0 && status.missing < status.total) {
        setCheckpointStatus(status)
        return
      }
    } catch {
      // If status check fails, just run all
    }
    startCrawl(undefined, 'all')
  }, [startCrawl])

  const handleRefreshPricesFromSettings = useCallback((mode: 'missing' | 'all') => {
    handleFindPrices(undefined, mode)
  }, [handleFindPrices])

  // One handler for both the bulk Refresh and a single store's, because the
  // feedback is the same either way: claim the button now, name what was
  // clicked in the status bar now, and only hand back to the real
  // stock_sync_* state once the server has answered.
  const startStockSync = useCallback(async (crawlerId?: number) => {
    const seq = ++latestStockSyncStartSeq.current
    setStockSyncStarting(crawlerId ?? 'all')
    const site = crawlerId != null ? crawlers.find((c) => c.id === crawlerId)?.site_name : null
    const what = site ? `${site} catalog refresh` : 'in-stock catalog refresh'
    stockSyncClaimNotice.current = {
      shown: `Starting ${what}…`,
      lost: `Lost track of the ${what} — reload to check whether it is still running.`,
    }
    setBusyStatusMessage(stockSyncClaimNotice.current.shown)
    setSyncStatus(stockSyncClaimNotice.current.shown)
    try {
      const result = await postStockSyncStart(crawlerId)
      if (seq !== latestStockSyncStartSeq.current) return
      reportStockSyncRejection(result, setSyncStatus)
      // On an accepted start the optimistic state stays until
      // stock_sync_started replaces it; a rejection ends here.
      if (!result.started) {
        setStockSyncStarting(null)
        setBusyStatusMessage(null)
      }
    } catch (e: any) {
      if (seq !== latestStockSyncStartSeq.current) return
      setStockSyncStarting(null)
      setBusyStatusMessage(null)
      setSyncStatus(`In-stock sync failed to start: ${e.message}`)
    }
  }, [crawlers, setSyncStatus])

  const handleRefreshStock = useCallback(() => startStockSync(), [startStockSync])

  const handleRefreshStoreCrawler = useCallback(
    (crawlerId: number) => startStockSync(crawlerId),
    [startStockSync],
  )

  const handleRefreshRecommendations = useCallback(async () => {
    try {
      await postJudgmentStart()
    } catch (e: any) {
      setSyncStatus(`Refresh recommendations failed to start: ${e.message}`)
    }
  }, [setSyncStatus])

  const handleExportRecommendations = useCallback(async () => {
    try {
      const blob = await exportRecommendationsCsv()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'recommendations.csv'
      a.click()
      URL.revokeObjectURL(url)
    } catch (e: any) {
      setSyncStatus(`Export recommendations failed: ${e.message}`)
    }
  }, [setSyncStatus])

  const handleImportRecommendations = useCallback(async (file: File) => {
    try {
      const r = await importRecommendationsCsv(file)
      if (r.running) {
        setSyncStatus('Cannot import recommendations while a sync or recommendation run is in progress')
        return
      }
      const applied = r.imported + r.updated
      const skippedClause = r.skipped > 0
        ? `. ${r.skipped} row${r.skipped === 1 ? '' : 's'} skipped`
        : ''
      if (applied === 0) {
        const base = r.unchanged > 0
          ? `Nothing new to import — ${r.unchanged} judgment${r.unchanged === 1 ? '' : 's'} already up to date`
          : 'No judgments imported'
        setSyncStatus(`${base}${skippedClause}.`)
      } else {
        let message = `Imported ${applied} judgment${applied === 1 ? '' : 's'}`
        if (r.unchanged > 0) message += `, ${r.unchanged} already current`
        // Nothing visibly changes in the Recommended filter until a store sync
        // surfaces these items, so say which ones are live now and which aren't.
        if (r.matched_stock_items === 0) {
          message += '. None in stock yet — they apply as items appear'
        } else {
          message += `. ${r.matched_stock_items} in stock now`
          if (r.matched_stock_items < applied) message += '; the rest apply as items appear'
        }
        setSyncStatus(`${message}${skippedClause}.`)
      }
      refreshJudgmentStatus()
    } catch (e: any) {
      let message = e.message || 'Import failed'
      try {
        const parsed = JSON.parse(e.message)
        if (parsed.detail) message = parsed.detail
      } catch {
        // not JSON, use raw message
      }
      setSyncStatus(`Import recommendations failed: ${message}`)
    }
  }, [setSyncStatus, refreshJudgmentStatus])

  const handleClearRecommendations = useCallback(async () => {
    if (!window.confirm('Clear all recommendations? This removes every recommended and not-recommended judgment from the database — every Store item will need to be re-evaluated from scratch, which costs Anthropic API calls to redo.')) {
      return
    }
    try {
      const result = await clearJudgments()
      if (!result.cleared) {
        setSyncStatus('Cannot clear recommendations while a sync or recommendation run is in progress')
        return
      }
      latestHasJudgedItemsSeq.current++
      setHasJudgedItems(false)
      setSyncStatus(`Cleared ${result.count} recommendation judgments`)
    } catch (e: any) {
      setSyncStatus(`Clear recommendations failed: ${e.message}`)
    }
  }, [setSyncStatus])

  // useCallback keeps this referentially stable across renders so it doesn't
  // defeat Account's memo() — see viewRenderChurn.test.tsx, which asserts
  // Account isn't re-invoked on every crawl SSE event.
  const toggleViewAsUser = useCallback(() => {
    setViewAsUser((current) => {
      const next = !current
      localStorage.setItem(VIEW_AS_USER_KEY, String(next))
      return next
    })
  }, [])

  if (backendUp === false && authState?.state !== 'authenticated') {
    return <BackendDownScreen />
  }
  if (authState === null) {
    return <div className="min-h-screen flex items-center justify-center text-gray-500">Loading…</div>
  }
  if (authState.state !== 'authenticated' && signupToken) {
    return (
      <InviteCodeScreen
        signupToken={signupToken}
        onRedeemed={() => {
          setSignupToken(null)
          window.history.replaceState({}, '', window.location.pathname)
          getAuthStatus().then(setAuthState)
        }}
      />
    )
  }
  if (authState.state === 'unauthenticated') {
    return <LoginScreen />
  }

  const isRealAdmin = authState.user.is_admin
  const showAdminNav = isRealAdmin && !viewAsUser

  const recommendedAvailable = hasAnthropicKey && hasJudgedItems
  // The optimistic target only stands in while there is no real one: a
  // per-crawler Refresh rejected by an already-running bulk sync must keep
  // showing the bulk sync, not the row that was just clicked.
  const stockSyncActive = stockSyncTarget ?? stockSyncStarting
  // A message the user is still waiting on keeps the banner's spinner up and
  // its Dismiss button away -- a Dismiss button next to "Starting…" reads as
  // finished. `syncing` is the same shape one level up and has the same drift
  // (a locally-generated message shown mid-sync still spins); it predates this
  // and fixing it means giving the shared status bar a full ownership model,
  // which is a bigger change than this one.
  const syncBusy = syncing || (busyStatusMessage !== null && syncMessage === busyStatusMessage)
  const syncBannerVisible = syncMessage !== null && (syncMessageId === null || syncMessageId > dismissedSyncId)
  const crawlBannerVisible = crawlBannerId > dismissedCrawlId

  function dismissSyncMessage() {
    if (syncMessageId !== null) {
      localStorage.setItem(DISMISSED_SYNC_KEY, String(syncMessageId))
      setDismissedSyncId(syncMessageId)
    }
    setSyncMessage(null)
  }

  function dismissCrawlBanner() {
    localStorage.setItem(DISMISSED_CRAWL_KEY, String(crawlBannerId))
    setDismissedCrawlId(crawlBannerId)
  }

  return (
    <div className="h-screen bg-gray-950 text-gray-100 flex flex-col overflow-hidden">
      {/* Wrapper is `inert` while the backend is confirmed down, or while a
          post-recovery session revalidation is still in flight, so a
          keyboard or screen-reader user can't tab into the frozen app
          underneath the BackendDownScreen overlay. `display: contents` keeps
          it invisible to layout -- header/main/etc. stay direct flex
          children of the h-screen container above. */}
      <div inert={backendUp === false || authRevalidating} className="contents">
      {/* Header */}
      <header className="bg-gray-900 border-b border-gray-800 px-6 py-3 flex items-center gap-4">
        <nav className="flex gap-2">
          <button
            onClick={() => setView('collection')}
            className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'collection')}`}
          >
            Collection
          </button>
          <button
            onClick={() => setView('wantlist')}
            className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'wantlist')}`}
          >
            Wantlist
          </button>
          <button
            onClick={() => setView('store')}
            className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'store')}`}
          >
            Store
          </button>
          <button
            onClick={() => setView('track')}
            className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'track')}`}
          >
            Track
          </button>
        </nav>
        <nav className="flex items-center gap-2 ml-auto">
          {showAdminNav && (
            <button
              onClick={() => setView('queue')}
              className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'queue')}`}
            >
              Queue
            </button>
          )}
          {showAdminNav && (
            <button
              onClick={() => setView('logs')}
              className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'logs')}`}
            >
              Logs
            </button>
          )}
          {showAdminNav && (
            <button
              onClick={() => setView('settings')}
              className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'settings')}`}
            >
              Settings
            </button>
          )}
          <button
            onClick={() => setView('account')}
            aria-label="Profile"
            className={`w-8 h-8 rounded-full overflow-hidden flex items-center justify-center transition-colors ${
              view === 'account' ? 'ring-2 ring-white' : 'hover:ring-2 hover:ring-gray-600'
            }`}
          >
            <Avatar version={avatarVersion} size="sm" />
          </button>
        </nav>
      </header>

      {/* Main */}
      <main className="flex-1 overflow-hidden">
        <div className={view === 'collection' ? 'h-full' : 'hidden'}>
          <RecordBrowser
            scope="collection"
            syncing={syncing}
            onRefreshCollection={() => handleRefresh()}
            syncGeneration={syncGeneration}
            hasPriceField={hasPriceData}
          />
        </div>
        <div className={view === 'wantlist' ? 'h-full' : 'hidden'}>
          <RecordBrowser
            scope="wantlist"
            syncing={syncing}
            onRefreshCollection={() => handleRefreshWantlist()}
            syncGeneration={syncGeneration}
            hasPriceField={hasPriceData}
          />
        </div>
        <div className={view === 'store' ? 'h-full' : 'hidden'}>
          <StockBrowser recommendedAvailable={recommendedAvailable} hiddenCrawlerIds={hiddenCrawlerIds} crawlers={crawlers} onHiddenCrawlerIdsChange={updateHiddenCrawlerIds} hiddenCrawlerIdsLoaded={hiddenCrawlerIdsLoaded} syncGeneration={stockSyncGeneration} isAdmin={showAdminNav} />
        </div>
        <div className={view === 'track' ? 'h-full' : 'hidden'}>
          <StockBrowser scope="track" hiddenCrawlerIds={hiddenCrawlerIds} crawlers={crawlers} onHiddenCrawlerIdsChange={updateHiddenCrawlerIds} hiddenCrawlerIdsLoaded={hiddenCrawlerIdsLoaded} syncGeneration={stockSyncGeneration} isAdmin={showAdminNav} hasPriceField={hasPriceData} />
        </div>
        <div className={view === 'settings' ? 'h-full overflow-y-auto' : 'hidden'}>
          <Settings
            crawlers={crawlers}
            onCrawlersChange={setCrawlers}
            onRefreshPrices={handleRefreshPricesFromSettings}
            onRefreshStock={handleRefreshStock}
            isAdmin={showAdminNav}
            stockSyncBusy={stockSyncActive !== null}
            stockSyncCrawlerId={typeof stockSyncActive === 'number' ? stockSyncActive : null}
            priceRefreshBusy={priceRefreshStarting}
            onRefreshStoreCrawler={handleRefreshStoreCrawler}
          />
        </div>
        <div className={view === 'account' ? 'h-full overflow-y-auto' : 'hidden'}>
          <Account
            avatarVersion={avatarVersion}
            onAvatarChange={setAvatarVersion}
            isAdmin={isRealAdmin}
            viewingAsUser={viewAsUser}
            onToggleViewAsUser={toggleViewAsUser}
            onRefreshRecommendations={handleRefreshRecommendations}
            onExportRecommendations={handleExportRecommendations}
            onImportRecommendations={handleImportRecommendations}
            onClearRecommendations={handleClearRecommendations}
            hasJudgedItems={hasJudgedItems}
          />
        </div>
        {/* Gated on showAdminNav, not just hidden: LogViewer opens its SSE
            stream on mount regardless of visibility, so mounting it for every
            user would hand each one an open stream of the operator's log. */}
        {showAdminNav && <div className={view === 'logs' ? 'h-full' : 'hidden'}><LogViewer /></div>}
        {/* Gated on showAdminNav for the same reason LogViewer is, and mounted
            only while it is the active view: QueueView polls the queue on a
            timer from mount, so a hidden-but-mounted copy would keep querying
            in the background behind whatever tab the admin is actually on. */}
        {showAdminNav && view === 'queue' && <div className="h-full"><QueueView /></div>}
      </main>

      {/* Collection refresh modal */}
      {collectionStatus && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-gray-900 border border-gray-700 rounded-xl shadow-xl p-6 w-96 max-w-full mx-4">
            <h2 className="text-white font-semibold text-lg mb-2">Collection already loaded</h2>
            <p className="text-gray-400 text-sm mb-1">
              <span className="text-white font-medium">{collectionStatus.total}</span> records in your collection.
            </p>
            {collectionStatus.last_synced && (
              <p className="text-gray-500 text-xs mb-5">
                Last synced: {new Date(collectionStatus.last_synced).toLocaleString()}
              </p>
            )}
            <div className="flex gap-3">
              <button
                onClick={() => startRefresh('new')}
                className={`flex-1 px-4 py-2 text-sm ${primaryButtonClass()}`}
              >
                Refresh New Only
                <span className="block text-xs font-normal text-gray-600">Skip existing records</span>
              </button>
              <button
                onClick={() => startRefresh('all')}
                className={`flex-1 px-4 py-2 text-sm ${secondaryButtonClass()}`}
              >
                Refresh All
                <span className="block text-xs font-normal text-gray-400">Re-sync {collectionStatus.total} records</span>
              </button>
            </div>
            <button
              onClick={() => setCollectionStatus(null)}
              className={`mt-3 w-full px-4 py-1.5 text-sm ${dismissButtonClass()}`}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Checkpoint modal */}
      {checkpointStatus && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-gray-900 border border-gray-700 rounded-xl shadow-xl p-6 w-96 max-w-full mx-4">
            <h2 className="text-white font-semibold text-lg mb-2">Resume previous run?</h2>
            <p className="text-gray-400 text-sm mb-1">
              <span className="text-white font-medium">{checkpointStatus.missing}</span> of{' '}
              <span className="text-white font-medium">{checkpointStatus.total}</span> records are missing prices.
            </p>
            {checkpointStatus.oldest_checked && (
              <p className="text-gray-500 text-xs mb-5">
                Last updated: {new Date(checkpointStatus.oldest_checked).toLocaleString()}
              </p>
            )}
            <div className="flex gap-3">
              <button
                onClick={() => startCrawl(undefined, 'missing')}
                className={`flex-1 px-4 py-2 text-sm ${primaryButtonClass()}`}
              >
                Resume
                <span className="block text-xs font-normal text-gray-600">{checkpointStatus.missing} records</span>
              </button>
              <button
                onClick={() => startCrawl(undefined, 'all')}
                className={`flex-1 px-4 py-2 text-sm ${secondaryButtonClass()}`}
              >
                Restart
                <span className="block text-xs font-normal text-gray-400">{checkpointStatus.total} records</span>
              </button>
            </div>
            <button
              onClick={() => setCheckpointStatus(null)}
              className={`mt-3 w-full px-4 py-1.5 text-sm ${dismissButtonClass()}`}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Server startup overlay */}
      {!serverReady && (
        <div className="fixed inset-0 bg-gray-950/90 flex flex-col items-center justify-center z-50 gap-4">
          <div className="w-8 h-8 border-2 border-white border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {/* Collection sync status bar */}
      {syncBannerVisible && (
        <div
          role="status"
          className="fixed bottom-0 left-0 right-0 bg-gray-900 border-t border-gray-700 px-4 py-2 flex items-center gap-3"
        >
          <span className="text-sm font-medium text-gray-300 shrink-0">
            {syncMessage}
          </span>
          {syncBusy && (
            <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin shrink-0" />
          )}
          {!syncBusy && (
            <button
              onClick={dismissSyncMessage}
              className={`ml-auto px-3 py-1 text-sm shrink-0 ${dismissButtonClass()}`}
            >
              Dismiss
            </button>
          )}
        </div>
      )}

      {/* Crawl status bar */}
      {crawlBannerVisible && !syncBannerVisible && (
        <div className="fixed bottom-0 left-0 right-0 bg-gray-900 border-t border-gray-700 px-4 py-2 flex items-center gap-3">
          <span className="text-sm font-medium text-gray-300 shrink-0">
            {crawling ? 'Refreshing prices…' : 'Done'}
          </span>
          {crawling && crawlCurrent && (
            <span className="text-sm text-gray-400 truncate">
              {crawlTotal > 0 ? `${crawlCount}/${crawlTotal}: ` : ''}
              <span className="text-gray-200">{crawlCurrent.artist} — {crawlCurrent.release}</span>
              {' '}on{' '}
              <span className="text-gray-300">{crawlCurrent.site}</span>
            </span>
          )}
          {!crawling && (
            <button
              onClick={dismissCrawlBanner}
              className={`ml-auto px-3 py-1 text-sm shrink-0 ${dismissButtonClass()}`}
            >
              Dismiss
            </button>
          )}
        </div>
      )}
      </div>

      {/* Backend down overlay -- shown on top of the still-mounted (but now
          inert) app so in-progress state (search filters, unsaved Settings
          fields) survives a transient outage instead of being unmounted.
          Stays up through authRevalidating too, so recovery never exposes
          the stale authenticated app before its session is reconfirmed. */}
      {(backendUp === false || authRevalidating) && <BackendDownScreen />}
    </div>
  )
}
