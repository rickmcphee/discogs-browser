import { useState, useEffect, useCallback, useRef } from 'react'
import RecordBrowser from './views/RecordBrowser'
import StockBrowser from './views/StockBrowser'
import Settings from './views/Settings'
import Account from './views/Account'
import LogViewer from './views/LogViewer'
import QueueView from './views/QueueView'
import Notifications from './views/Notifications'
import LoginScreen from './views/LoginScreen'
import InviteCodeScreen from './views/InviteCodeScreen'
import BackendDownScreen from './views/BackendDownScreen'
import Avatar from './components/Avatar'
import BottomNav from './components/BottomNav'
import NotificationBell from './components/NotificationBell'
import Sheet from './components/Sheet'
import { useIsMobile } from './hooks/useMediaQuery'
import { navButtonClass, primaryButtonClass, secondaryButtonClass, dismissButtonClass } from './styles/buttons'
import { refreshCollection, getCollectionStatus, openCrawlStream, getCrawlStatus, postCrawlStart, postStockSyncStart, postJudgmentStart, postJudgmentStop, clearJudgments, exportRecommendationsCsv, importRecommendationsCsv, getCrawlers, getUserSettings, getUserHiddenCrawlers, postUserHiddenCrawlers, getJudgmentStatus, getPriceStatus, getNotificationsUnread, markNotificationsRead, checkHealth, getAuthStatus, setUnauthorizedHandler, hasAvatar } from './api/client'
import type { StockSyncStartResult } from './api/client'
import type { CrawlEvent, CrawlStatus, CollectionStatus, CollectionSyncRun, Crawler, AuthStatus, JudgmentStatus } from './api/types'

type View = 'collection' | 'wantlist' | 'store' | 'settings' | 'logs' | 'queue' | 'account' | 'notifications'
type LibraryView = Extract<View, 'collection' | 'wantlist' | 'store'>

// One table drives the desktop header pills and the mobile tab bar, so the two
// cannot end up offering different sets of tabs.
const LIBRARY_TABS: { view: LibraryView; label: string; icon: 'collection' | 'wantlist' | 'store' }[] = [
  { view: 'collection', label: 'Collection', icon: 'collection' },
  { view: 'wantlist', label: 'Wantlist', icon: 'wantlist' },
  { view: 'store', label: 'Store', icon: 'store' },
]

// Admin-only, and rarely visited -- header pills on desktop, an overflow sheet
// on mobile, where the header has room for a title and a short row of controls
// (the notification bell and the avatar, plus this menu's own button).
const ADMIN_TABS: { view: View; label: string }[] = [
  { view: 'queue', label: 'Queue' },
  { view: 'logs', label: 'Logs' },
  { view: 'settings', label: 'Settings' },
]

// SSE reconnects (including on browser refresh) replay every buffered event from
// crawl_manager._recent, so a banner's dismissal has to survive across that replay.
// Each broadcast event carries a monotonic `id`; we persist the id of the last-dismissed
// event and only show a banner when the current event's id is newer than that.
const DISMISSED_SYNC_KEY = 'discogs-browser.dismissedSyncEventId'
const DISMISSED_CRAWL_KEY = 'discogs-browser.dismissedCrawlEventId'
const VIEW_AS_USER_KEY = 'discogs-browser.viewAsUser'

// How long a click may hold its Refresh button disabled and spinning before
// the app admits it does not know and lets go. Both claims need it, for the
// same reason from opposite ends:
//
//   - A stock claim normally hands over to stock_sync_started within a second,
//     but nothing guarantees that event arrives. The SSE stream can break and
//     reconnect across an entire short sync, and routers/crawl.py's
//     _events_to_replay returns nothing once no job is active -- so a sync
//     that both started and finished inside the gap replays neither event.
//   - A price claim covers its own request, and apiFetch wraps a plain fetch
//     with no timeout or abort signal, so a stalled POST never settles.
//
// Unbounded, either leaves a button the user cannot retry from -- the stuck
// state this whole change exists to remove, reached from the inside. Releasing
// is self-correcting: a late stock_sync_started takes the button straight
// back, a late response is dropped by the sequence guard, and a click during
// work the UI has lost track of is rejected by the server rather than starting
// anything twice.
const START_CLAIM_TIMEOUT_MS = 20_000

// How often the collection tab asks Postgres how its sync is getting on.
//
// It has to ask, because it cannot rely on being told. The sync_* events that
// narrate a run are broadcast in-process (CrawlManager's _subscribers), and
// the deployment runs more than one Machine behind one hostname with no
// affinity between requests -- so this browser's SSE stream and the POST
// /collection/refresh it just sent need not have landed on the same one. When
// they don't, no sync_started, no sync_progress and no sync_complete ever
// reaches this tab: the button never spins, the banner stays empty, and the
// collection table -- which refetches only when one of those arrives -- keeps
// showing the library as it was before the sync, new Discogs additions and
// all. The sync ran; nothing here heard it.
//
// The run is also written to a row both Machines can read (library_sync_runs),
// which is what this polls. The SSE handlers stay as the same-Machine fast
// path; both drive the same state, and a doubled progress tick costs one extra
// refetch and nothing else.
const COLLECTION_SYNC_POLL_MS = 3000

// What a refused start says once the poll finds no sync to show for it.
// Deliberately not "a sync is already running": POST /collection/refresh
// answers 409 for every reason start_sync declines, and a Plex match for this
// user is one of them -- in which case there is no collection sync to follow
// and the click would otherwise pass in silence.
// How many failed status reads the poll sits through before giving up, when
// it is not yet following anything -- a refused start waiting to learn what
// refused it, or the mount-time read looking for a sync already under way. A
// sync being followed retries indefinitely instead: it is running, and its
// outcome is worth waiting for.
const POLL_READ_ATTEMPTS = 3

// Same gap, same remedy, for the recommendation run: stock_judgment_* events
// reach only the Machine running the job, so the Refresh/Stop button cannot be
// driven by them alone -- half the time none of them arrive. This polls
// stock_judgment_runs through GET /stock/judge/status, and only while a run is
// believed to be under way; the SSE handlers stay as the same-Machine fast
// path, and both write the same two flags.
const JUDGMENT_RUN_POLL_MS = 3000

const REFUSED_START_MESSAGE =
  'Could not start a sync — another job is running for your account. Try again shortly.'

function collectionSyncProgressMessage(run: CollectionSyncRun): string {
  if (run.scope === 'wishlist') return 'Syncing wantlist…'
  if (run.total_pages) {
    return `Syncing collection… ${run.synced} records (page ${run.page}/${run.total_pages})`
  }
  return 'Syncing collection…'
}

// No username, unlike the sync_complete event's version of this line: the run
// row records the sync, not who authenticated it, and the user reading their
// own banner already knows.
function collectionSyncOutcomeMessage(run: CollectionSyncRun): string {
  // A run whose Machine restarted mid-sync stops heartbeating and never
  // finishes. Saying so is the whole recovery -- the click that follows is
  // no longer refused, because the claim has gone stale with it.
  //
  // Only while the *sync* is the phase that went quiet, though. A stale
  // `plex_matching` row is a Plex match that died after the sync itself
  // committed its rows and its final counts, and reporting that as an
  // unfinished sync would send the user back to redo work that is already
  // done. The sync's own outcome is the honest line there.
  if (run.stale && run.status === 'running') {
    return 'Sync stopped before it finished — sync again to pick up where it left off.'
  }
  if (run.status === 'error') return `Sync failed: ${run.error ?? 'unknown error'}`
  if (run.scope === 'wishlist') return `Synced ${run.wishlist_synced ?? 0} wantlist items`
  const wantlistPart = run.wishlist_synced != null ? `, ${run.wishlist_synced} wantlist items` : ''
  return `Synced ${run.synced} records${wantlistPart}`
}

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
  const isMobile = useIsMobile()
  // Mobile-only state, and the sheet holding it unmounts at the breakpoint --
  // so without this a menu left open in portrait quietly reopens itself on the
  // way back from landscape.
  const [adminMenuOpen, setAdminMenuOpen] = useState(false)
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
  const [unreadNotifications, setUnreadNotifications] = useState(0)
  const latestNotificationsSeq = useRef(0)
  // Same race, same fix, for the two start requests. Neither has a bounded
  // response time -- POST /stock/sync/start opens a fresh psycopg connection
  // before it answers -- and the claim timeout below can re-enable the button
  // while one is still in flight. Without these, a first request rejecting
  // after a second click would clear the *newer* claim and overwrite its
  // status with the older request's result.
  const latestStockSyncStartSeq = useRef(0)
  const latestPriceRefreshSeq = useRef(0)
  // Those two are per-operation: each decides whether a response may still
  // touch its own claim. Neither can decide whether it may touch the *status
  // bar*, which both write and which Settings lets them contend for -- a stock
  // start and a price refresh can be in flight at once. Without a shared
  // token, an older stock rejection lands on top of a newer price refresh's
  // "Starting…" while its request is still running.
  // Cleanup of a claim's own state stays on the per-operation guard, since a
  // request that lost the banner must still release the button it took.
  //
  // Only the price path advances it, because only the price path writes on
  // click. A stock click reads it instead: reading is enough to be invalidated
  // by a later price click, which is the case above, and advancing it would
  // silence a price response the stock click is not going to replace -- its
  // own start writes nothing, so the "Starting…" line would stay up, spinning
  // and undismissable, with the timer that could have retired it already
  // cancelled by that discarded response's own cleanup.
  const latestStatusOwnerSeq = useRef(0)
  const latestHasJudgedItemsSeq = useRef(0)
  // The recommendation run, reduced to the two things the button renders.
  // `stopping` is not a UI affectation: a stop is a flag the run reads between
  // batches, so the batch in flight has to finish first, and a button that
  // snapped straight back to Refresh would invite a second start against a run
  // that is still going -- which the claim would then refuse, silently.
  const [recommendationRunning, setRecommendationRunning] = useState(false)
  const [recommendationStopping, setRecommendationStopping] = useState(false)
  // Same race, same fix, as latestHasJudgedItemsSeq guards for hasJudgedItems:
  // the poll, the start and stop responses, and the SSE handlers are four
  // writers of these flags, and a slow read must not land on top of a newer
  // answer. Every direct writer bumps this first.
  const latestJudgmentRunSeq = useRef(0)
  // Counts *user actions* on the run -- a Refresh or a Stop click -- and
  // nothing else. Separate from the counter above because that one is also
  // bumped by every status read, including a handler's own recovery read: a
  // handler comparing against it after awaiting its own read always finds
  // itself superseded, and silently drops the verdict it went to fetch. What a
  // handler actually needs to know is whether the *user* has since asked for
  // something else.
  const latestJudgmentActionSeq = useRef(0)
  // True while a stop the user asked for is in flight. A status read issued in
  // that window is not authoritative about the stop flag -- it can reach the
  // row before the stop commits and answer "not stopping" about a stop that is
  // already on its way -- so it is allowed to refresh everything else and not
  // these two flags. Without this the poll re-enables the button mid-request
  // and the stop's own reply, being older than that read, is then discarded.
  const judgmentStopPending = useRef(false)
  // The last (status, judged) the poll saw, so it can tell a run that has
  // advanced from one it has merely been asked about again. Null means "no
  // read yet", and only that very first read is exempt from the bump -- it is
  // "what is the state on load" rather than movement, and would otherwise
  // refetch the Store on every page load.
  const lastJudgmentRunSeen = useRef<string | null>(null)
  const [serverReady, setServerReady] = useState(false)
  const [backendUp, setBackendUp] = useState<boolean | null>(null)
  const [authRevalidating, setAuthRevalidating] = useState(false)
  const [syncMessage, setSyncMessage] = useState<string | null>(null)
  const [syncMessageId, setSyncMessageId] = useState<number | null>(null)
  const [dismissedSyncId, setDismissedSyncId] = useState(() => Number(localStorage.getItem(DISMISSED_SYNC_KEY) ?? 0))
  const [syncing, setSyncing] = useState(false)
  const [syncGeneration, setSyncGeneration] = useState(0)
  const [stockSyncGeneration, setStockSyncGeneration] = useState(0)
  // A strict subset of stockSyncGeneration: bumped only by the events that can
  // actually have written a price, so the notification badge and view are not
  // woken by work that cannot produce a price drop. stockSyncGeneration is also
  // bumped by the judgment events, which write stock_item_judgments and never
  // touch a price -- riding it would fire an unread request per judgment batch,
  // and, with the Notifications tab open, a list reload and a read POST too.
  const [priceGeneration, setPriceGeneration] = useState(0)
  // Two more strict subsets of stockSyncGeneration, for the same reason
  // priceGeneration is one. The Store tab's Stats panel counts stock_items
  // rows, so a listing_changed -- which writes listings and is broadcast
  // globally to every connected user -- can never change what it shows, and
  // riding the union would fire a grouped count per marketplace write, for
  // every user with the panel open, during a crawl. The item list beside it
  // does need listing_changed, because its comparison rows are listings; the
  // two signals differ because the two views count different things.
  const [stockInventoryGeneration, setStockInventoryGeneration] = useState(0)
  // Judgments only move the Recommended filter, so the panel adds this one
  // conditionally rather than always.
  const [stockJudgmentGeneration, setStockJudgmentGeneration] = useState(0)
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

  // Counts every write to the banner. The price claim's expiry needs to know
  // whether anything has spoken since the claim was taken, and counting is the
  // only way to tell -- recognising its own text on screen breaks the moment
  // another message happens to read the same. So this has to be the only way a
  // message reaches the banner: a write that skips it is invisible to the
  // guard, and the guard then talks over it.
  const statusWrites = useRef(0)

  // eventId is null for locally-generated messages (button-click failures) that never
  // survive a refresh and so never need replay suppression; those always show.
  const setSyncStatus = useCallback((message: string, eventId: number | null = null) => {
    statusWrites.current++
    setSyncMessage(message)
    setSyncMessageId(eventId)
  }, [])

  // A stock claim says nothing in the banner from either end: the clicked
  // button spins for itself, and stock_sync_started overwrites the banner a
  // beat later anyway, so a "Starting…" line only repeated what the button
  // already showed and then flickered away. Its expiry is no louder. Naming
  // the store and pointing at the Logs tab read as a fault report, but the
  // ordinary cause is a stream that missed the events for a sync that ran
  // fine, and the Logs tab shows the user nothing they can act on. Releasing
  // the button is the whole of the recovery: a late stock_sync_started takes
  // it straight back, and a click during a sync the UI has lost track of is
  // rejected by the server rather than starting anything twice. Bumping the
  // sequence first drops the request this claim was waiting on.
  useEffect(() => {
    if (stockSyncStarting === null) return
    const timer = setTimeout(() => {
      latestStockSyncStartSeq.current++
      setStockSyncStarting(null)
    }, START_CLAIM_TIMEOUT_MS)
    return () => clearTimeout(timer)
  }, [stockSyncStarting])

  // The price claim's own bound. Unlike the stock one it does speak on expiry,
  // because it wrote a "Starting…" line on the way in that nothing else will
  // ever replace: POST /crawl/start's reply is the only confirmation that
  // exists, so a stalled request leaves that line standing forever. It used to
  // guard itself by comparing the displayed text against that message, which
  // wrote through setSyncMessage and so never reached the counter -- and a
  // write the counter cannot see is one a later expiry mistakes for silence
  // and talks over. One idiom, one write path. Bumping the sequence first
  // makes the stalled response a no-op if it ever does land.
  const priceClaimNotice = useRef<{ writes: number; lost: string } | null>(null)

  useEffect(() => {
    if (!priceRefreshStarting) return
    const timer = setTimeout(() => {
      latestPriceRefreshSeq.current++
      setPriceRefreshStarting(false)
      const notice = priceClaimNotice.current
      if (notice && statusWrites.current === notice.writes) setSyncStatus(notice.lost)
    }, START_CLAIM_TIMEOUT_MS)
    return () => clearTimeout(timer)
  }, [priceRefreshStarting, setSyncStatus])

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

  // Same counter, same reason: the bootstrap fetch, every SSE generation tick,
  // and the write that follows opening the Notifications tab can all be in
  // flight at once, and a slow bootstrap response landing last would relight a
  // dot the user has just cleared.
  const fetchUnreadNotifications = useCallback(() => {
    const seq = ++latestNotificationsSeq.current
    getNotificationsUnread().then((s) => {
      if (seq !== latestNotificationsSeq.current) return
      setUnreadNotifications(s.unread)
    }).catch(() => {})
  }, [])

  // Opening the tab is what marks it read -- but the badge only follows the
  // write, never runs ahead of it. Clearing optimistically would make a
  // dropped POST look like success, and the price change that produced those
  // unread rows has already happened, so nothing guarantees a later generation
  // tick to correct it: the dot would stay wrong until an unrelated change or
  // a reload. Waiting costs one round trip the user does not see, since the
  // list they came for is already on screen by then.
  const handleNotificationsLoaded = useCallback((latestId: number | null) => {
    if (latestId === null) {
      // Re-read rather than assume zero. The payload is one snapshot, so an
      // empty list really did mean nothing was unread -- at the moment the
      // server took it. A drop committing immediately after leaves this branch
      // holding a count that is already stale, and forcing zero would then hide
      // a dot the server had raised, with no guaranteed later tick to correct
      // it. The fetch also takes a newer token, which is what the bump was for.
      fetchUnreadNotifications()
      return
    }
    const seq = ++latestNotificationsSeq.current
    markNotificationsRead(latestId).then((s) => {
      if (seq === latestNotificationsSeq.current) {
        setUnreadNotifications(s.unread)
        return
      }
      // A read overlapped this write and already claimed the token, so the
      // count above cannot be applied. That read may also be stale: a
      // generation tick can start a GET after this POST began and the server
      // can still answer it from before the watermark commits, which would
      // relight a dot the user had just cleared. Re-reading is what settles
      // it -- this request is issued strictly after the write committed, and
      // takes a newer token than the read that overtook us.
      fetchUnreadNotifications()
    }).catch(() => {})
  }, [fetchUnreadNotifications])

  // Same race, same fix, for hasJudgedItems: the bootstrap fetch below and
  // handleImportRecommendations's post-import refresh can both have a
  // getJudgmentStatus() request in flight, and the judgment SSE handlers and
  // handleClearRecommendations's explicit write are two more sources that
  // can land in between. Every writer shares this one counter -- the SSE
  // handlers and the clear handler bump it before writing directly (they
  // already know the answer, no fetch needed), so a slower fetch that was
  // already in flight loses the race and its stale result is discarded.
  const refreshJudgmentStatus = useCallback(async (): Promise<JudgmentStatus | null> => {
    const judgedSeq = ++latestHasJudgedItemsSeq.current
    const runSeq = ++latestJudgmentRunSeq.current
    try {
      const s = await getJudgmentStatus()
      if (judgedSeq === latestHasJudgedItemsSeq.current) setHasJudgedItems(s.any_judged)
      // Two counters, not one: the same reply carries both answers, but the
      // writers that can overtake it differ -- a clear writes hasJudgedItems
      // and says nothing about the run, a stop response the reverse -- so a
      // single guard would let either discard the half it knows nothing about.
      if (runSeq !== latestJudgmentRunSeq.current) {
        // Superseded. Returning it anyway would let the poll below read a
        // terminal answer about an *older* run -- one that arrived after a
        // newer start had already set the flags -- and stop following the run
        // that is actually spending, leaving its button stuck on Stop.
        return null
      }
      if (!judgmentStopPending.current) {
        setRecommendationRunning(Boolean(s.run?.running))
        setRecommendationStopping(Boolean(s.run?.running && s.run.stop_requested))
      }
      // The judgments this run has written are invisible to an already-open
      // Store tab unless something tells it to refetch, and on the Machine
      // that is not running the job nothing does: the generation bumps live on
      // the stock_judgment_* handlers, which never arrive there. So the poll
      // makes them too, from the counters it can see -- gated on the run
      // having actually moved, or the poll would refetch every few seconds for
      // as long as a run lasts.
      // "no run" is a baseline like any other, not a reason to record nothing:
      // leaving the ref null through a mount-time `run: null` made the *next*
      // read look like the first one, so a short cross-Machine run that began
      // and ended between two reads suppressed the very bump it should have
      // caused.
      // started_at is in the key, not just status and count: two runs can end
      // identically -- a previous `complete/40` and a fresh one-batch run that
      // also reaches `complete/40` between two reads are indistinguishable
      // without it, and the Store would sit on the older run's judgments.
      const seen = `${s.run?.started_at ?? 'none'}/${s.run?.status ?? 'none'}/${s.run?.judged ?? 0}`
      if (seen !== lastJudgmentRunSeen.current) {
        if (lastJudgmentRunSeen.current !== null) {
          setStockSyncGeneration(g => g + 1)
          setStockJudgmentGeneration(g => g + 1)
        }
        lastJudgmentRunSeen.current = seen
      }
      return s
    } catch {
      // Never throws: every caller treats a failed read as "nothing new to
      // say", and the poll below decides for itself whether to keep trying.
      return null
    }
  }, [])

  // The discovery read, retried a bounded number of times, in the shape the
  // collection sync's own discovery poll already uses.
  //
  // A single attempt is not enough anywhere this is called. On the Machine that
  // is not running the job nothing else is coming -- no stock_judgment_* event
  // arrives, and the poll below only runs once a run is already believed to be
  // in flight -- so one dropped request leaves the button reading Refresh for
  // the whole of a paid run, with no way to stop it.
  // `waitForRun` is what a *failed start* needs and a page load does not. On a
  // page load, "no run" is a complete answer. After a start request that threw,
  // it is not: the POST can fail at the client while the server is still
  // committing the claim, so an immediate read can win that race and answer
  // `run: null` about a run that is about to exist. Ending there would hide it
  // for good, since nothing else is coming. Returns whether a run is under way.
  const discoverJudgmentRun = useCallback(async (waitForRun = false): Promise<boolean> => {
    for (let attempt = 0; attempt < POLL_READ_ATTEMPTS; attempt++) {
      const status = await refreshJudgmentStatus()
      if (status?.run?.running) return true
      if (status && !waitForRun) return false
      if (attempt < POLL_READ_ATTEMPTS - 1) {
        await new Promise(r => setTimeout(r, JUDGMENT_RUN_POLL_MS))
      }
    }
    return false
  }, [refreshJudgmentStatus])

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
    discoverJudgmentRun()
    fetchPriceStatus()
    hasAvatar().then((exists) => setAvatarVersion(exists ? Date.now() : 0)).catch(() => {})
  }, [authState, backendUp, serverReady, setSyncStatus, fetchPriceStatus, discoverJudgmentRun])

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
        // Noted, not acted on: the stream has spoken an outcome, so the poll
        // should not repeat it over whatever comes next (a plex_match_started
        // from the phase that follows a sync, most immediately). Deliberately
        // not a release of the follow -- routers/crawl.py replays this
        // process's whole retained buffer on reconnect, so this event may
        // belong to an earlier sync entirely, and dropping the follow on it
        // would lose the refetch for the run actually in flight. The poll
        // clears this again the moment it sees the run still running.
        sseAnnouncedOutcomeRef.current = true
        sseTerminalSeqRef.current += 1
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
        sseAnnouncedOutcomeRef.current = true
        sseTerminalSeqRef.current += 1
        setSyncing(false)
        setSyncStatus(`Sync failed: ${event.error}`, event.id ?? null)
        // Each page's writes (including price_paid) commit before the next page
        // starts, so a sync that fails partway through can still have changed
        // stored prices -- refetch regardless of which scope errored. The same
        // goes for the rows themselves: wantlist pages commit without a
        // sync_progress, so on a late failure this event is the only signal
        // the library views get that their rows moved.
        fetchPriceStatus()
        setSyncGeneration(g => g + 1)
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
        setStockInventoryGeneration(g => g + 1)
        setPriceGeneration(g => g + 1)
        return
      }
      if (event.status === 'stock_sync_complete') {
        setSyncing(false)
        setStockSyncTarget(null)
        setStockSyncStarting(null)
        setSyncStatus(`In-stock sync complete: ${event.synced} items`, event.id ?? null)
        setStockSyncGeneration(g => g + 1)
        setStockInventoryGeneration(g => g + 1)
        setPriceGeneration(g => g + 1)
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
        // The same-Machine fast path for the button: this browser heard the
        // run start, so it need not wait for the poll's first tick. A run
        // whose events go to the other Machine's subscribers reaches the same
        // state a beat later, through GET /stock/judge/status.
        //
        // `stopping` is deliberately left alone. This event can be queued
        // before a Stop click and delivered after it, and clearing the flag
        // there would turn the disabled "Stopping…" back into "Stop" while the
        // row's flag is still set -- inviting a second click that does
        // nothing. The row clears it, through the poll or a terminal event.
        latestJudgmentRunSeq.current++
        setRecommendationRunning(true)
        setSyncStatus('Finding recommendations for Store items…', event.id ?? null)
        return
      }
      if (event.status === 'stock_judgment_progress') {
        if ((event.judged ?? 0) > 0) {
          latestHasJudgedItemsSeq.current++
          setHasJudgedItems(true)
        }
        latestJudgmentRunSeq.current++
        setRecommendationRunning(true)
        setStockSyncGeneration(g => g + 1)
        setStockJudgmentGeneration(g => g + 1)
        setSyncStatus(`Finding recommendations for Store items… ${event.judged}/${event.total}`, event.id ?? null)
        return
      }
      if (event.status === 'stock_judgment_complete' || event.status === 'stock_judgment_stopped') {
        const stopped = event.status === 'stock_judgment_stopped'
        setSyncing(false)
        latestJudgmentRunSeq.current++
        setRecommendationRunning(false)
        setRecommendationStopping(false)
        // A judgment event names no run, so an ending delivered late -- this
        // Machine's buffer replaying it, or a slow queue -- is indistinguishable
        // from the current run's. Clearing the flags on it is right nearly
        // always and wrong exactly when a newer run has started since, where it
        // would take Stop away from a run still spending and stop the poll that
        // would have noticed. So the row gets the last word: one read, which
        // restores the flags if a run is in fact still going.
        refreshJudgmentStatus()
        if ((event.judged ?? 0) > 0) {
          latestHasJudgedItemsSeq.current++
          setHasJudgedItems(true)
        }
        setStockSyncGeneration(g => g + 1)
        setStockJudgmentGeneration(g => g + 1)
        setSyncStatus(
          stopped
            // Says what was kept, not just that it ended: the items judged
            // before the stop stay judged and are not paid for again, and the
            // rest are simply still unjudged for the next run to pick up.
            ? `Recommendation run stopped — ${event.judged} of ${event.total} items checked`
            : `Finished finding recommendations — ${event.judged} items checked`,
          event.id ?? null,
        )
        return
      }
      if (event.status === 'stock_judgment_error') {
        setSyncing(false)
        latestJudgmentRunSeq.current++
        setRecommendationRunning(false)
        setRecommendationStopping(false)
        // Confirmed against the row, same as the two endings above.
        refreshJudgmentStatus()
        setSyncStatus(`Finding recommendations failed: ${event.error}`, event.id ?? null)
        return
      }
      if (event.type === 'listing_changed') {
        setStockSyncGeneration(g => g + 1)
        // 'found' only. A 'not_found' writes no price at all on the stock-item
        // path, and on the release path only clears or deletes one, so neither
        // can have recorded a drop -- and most stock-item searches legitimately
        // find nothing, so counting them would fan a request out to every
        // connected user for the majority of crawl results. The Store tab
        // still wants both: a cleared price changes what it renders.
        if (event.status === 'found') setPriceGeneration(g => g + 1)
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
      // listing_changed is never buffered or replayed (see crawl_manager), and
      // the error path below only reopens the stream -- so a drop recorded
      // while the connection was down produces no tick at all, and the bell
      // would sit stale until an unrelated price event or a reload. A
      // generation bump rather than a bare count re-read: it refreshes the
      // badge through the effect below either way, and it is also the only
      // thing an already-open Notifications tab listens to. Refreshing just
      // the count relit the dot over a list that never re-ran, and clicking
      // that bell only sets the view it is already on.
      source.onopen = () => setPriceGeneration(g => g + 1)
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
  }, [authState, setSyncStatus, fetchPriceStatus, refreshJudgmentStatus])

  // Rides priceGeneration rather than a notification-specific SSE event: a
  // per-user event would have to be tagged with an owner, and the crawl worker
  // cannot determine one -- saves are RLS-scoped and invisible to it (see the
  // price-drop design doc). Every event that bumps priceGeneration is one that
  // could have just recorded a drop. Also covers the first fetch, since
  // serverReady only flips once bootstrap runs.
  useEffect(() => {
    if (authState?.state !== 'authenticated' || !serverReady) return
    fetchUnreadNotifications()
  }, [priceGeneration, authState, serverReady, fetchUnreadNotifications])

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

  // Follows the run row until it ends, and is what makes a refresh visible at
  // all when this tab's SSE stream is served by the other Machine -- see
  // COLLECTION_SYNC_POLL_MS. Restarted by bumping syncPollNonce; the ref
  // carries what the restart means.
  //
  // `adoptTerminal` says whether a run that is already finished may be
  // reported. A refresh the server accepted may well have finished before the
  // first poll, and its outcome is this click's answer -- but only the click
  // knows that, since the row itself looks the same as one from last week.
  // `idleMessage` is the other half: a refused start whose refusal turns out
  // not to be a running sync has to say *something*, or the click is the
  // silent no-op this whole change exists to remove.
  const [syncPollNonce, setSyncPollNonce] = useState(0)
  const syncPollIntentRef = useRef<{ adoptTerminal: boolean; idleMessage: string | null } | null>(null)
  const authed = authState?.state === 'authenticated'
  // Outside the effect, because the effect restarts and the follow must not.
  // `authState` is replaced with a fresh object after every backend down/up
  // transition, so a blip mid-sync would otherwise reset these: the restarted
  // loop would find a run that finished during the outage, take it for an old
  // one, and return without refetching -- the stale collection this whole
  // change exists to prevent, reached by a different road.
  const followingSyncRef = useRef(false)
  const lastSyncProgressRef = useRef('')
  // The banner line is tracked apart from the progress signature, because the
  // two move at different times. wishlist_synced advances at every wantlist
  // checkpoint and appears in no rendered line -- collectionSyncProgressMessage
  // reads page/total_pages/synced for a collection sync and returns a constant
  // for a wantlist one. Writing the banner off the signature therefore rewrote
  // it with identical text throughout the wantlist phase, which is invisible
  // on its own and clobbers whatever the stock sync, judgment run or price
  // refresh had put there since the last poll.
  const lastSyncMessageRef = useRef('')

  // Set when the stream speaks a terminal sync event, cleared the moment the
  // poll sees a run still running. It says only "an outcome has just been
  // published", which is all the poll needs to know not to publish it again;
  // it is not evidence about *which* run ended, because a replayed event
  // carries none.
  const sseAnnouncedOutcomeRef = useRef(false)
  // Bumped by the same terminal events. The flag says "an outcome was just
  // published"; this says *when*, which is what a poll response needs in order
  // to know whether it is still current. A status request reads the row before
  // the sync closes and can resolve after the stream has already reported the
  // ending -- and then the `running` it is holding is simply out of date.
  const sseTerminalSeqRef = useRef(0)

  // The run has been accounted for. Only the poll says this, and only about
  // the row it just read.
  const releaseSyncFollow = useCallback(() => {
    followingSyncRef.current = false
    lastSyncProgressRef.current = ''
    lastSyncMessageRef.current = ''
    sseAnnouncedOutcomeRef.current = false
  }, [])

  useEffect(() => {
    if (!authed) return
    let cancelled = false
    const intent = syncPollIntentRef.current
    syncPollIntentRef.current = null
    // Only a run this tab has actually watched may write its outcome to the
    // banner. Otherwise every page load would re-announce the last sync,
    // however old -- the row is the most recent run, not a fresh event. A
    // refresh the server accepted counts as watched: the claim is taken by the
    // request itself, so the run is already there to find.
    if (intent?.adoptTerminal) followingSyncRef.current = true
    const idleMessage = intent?.idleMessage ?? null
    let failedReads = 0

    async function poll() {
      while (!cancelled) {
        let status: CollectionStatus | null = null
        // Read before the request goes out: if a terminal event arrives while
        // it is in flight, the reply is describing a moment before that ending
        // and its `running` is stale, however fresh the response looks.
        const terminalSeqAtRequest = sseTerminalSeqRef.current
        try {
          status = await getCollectionStatus()
          failedReads = 0
        } catch {
          // A sync being followed is waited on for as long as it takes: it is
          // running, and its outcome is worth having. Everything else gets a
          // bounded number of tries -- a refused start, which has said nothing
          // yet and would otherwise be the silent refresh this whole change
          // exists to remove, and the mount-time read that discovers a sync
          // already under way, which since this effect stopped restarting on
          // revalidation has no second chance of its own.
          failedReads += 1
          if (!followingSyncRef.current && failedReads >= POLL_READ_ATTEMPTS) {
            // Out of tries. Say what the server already told us with its 409,
            // if it told us anything; a discovery read has nothing to report.
            if (idleMessage) setSyncStatus(idleMessage)
            return
          }
        }
        if (cancelled) return
        if (status) {
          const run: CollectionSyncRun | null = status.sync
          // No run at all: this user has never synced. Nothing here to follow.
          if (!run) {
            if (idleMessage) setSyncStatus(idleMessage)
            return
          }
          if (run.running && sseTerminalSeqRef.current !== terminalSeqAtRequest) {
            // The stream reported an ending while this request was in flight,
            // so the row was read before the sync closed. Acting on it would
            // undo the report: clearing the flag lets the next tick republish
            // the outcome over whatever came after it (the Plex phase
            // announces itself a beat later), and the progress line would talk
            // over that same newer status. Wait for a reply that was issued
            // after the ending instead -- the next tick's.
          } else if (run.running) {
            followingSyncRef.current = true
            // Whatever outcome the stream announced, it was not this run's --
            // this one is still going.
            sseAnnouncedOutcomeRef.current = false
            setSyncing(true)
            const progress = `${run.page}/${run.total_pages}/${run.synced}/${run.wishlist_synced}`
            if (progress !== lastSyncProgressRef.current) {
              lastSyncProgressRef.current = progress
              // Both writes are gated on the run having actually advanced, not
              // just on having been asked again. The banner is shared with the
              // stock sync, the judgment run and the price refresh, any of
              // which may be running alongside this one and may have written
              // to it since the last poll -- repeating an unchanged line every
              // three seconds would talk over all of them. The SSE path has
              // the same restraint for free: it only speaks when something
              // happened.
              //
              // The refetch is gated on the signature and the banner on the
              // line itself, because "the run advanced" and "there is
              // something new to say" are not the same event. Rows committed
              // under an unchanged line still have to be fetched; a line that
              // has not changed has nothing to add and would only be talking
              // over someone else.
              const message = collectionSyncProgressMessage(run)
              if (message !== lastSyncMessageRef.current) {
                lastSyncMessageRef.current = message
                setSyncStatus(message)
              }
              setSyncGeneration(g => g + 1)
            }
          } else if (followingSyncRef.current) {
            const alreadySpoken = sseAnnouncedOutcomeRef.current
            releaseSyncFollow()
            setSyncing(false)
            // The refetch happens either way -- it is the whole point, and the
            // one thing that must not be lost. The line is skipped only when
            // the stream has just said the same thing.
            if (!alreadySpoken) setSyncStatus(collectionSyncOutcomeMessage(run))
            // Unconditional, not gated on the counters having moved: this is
            // the tick that pulls in everything the last page committed, and
            // on a sync whose pages all landed between two polls it is the
            // only one there is.
            setSyncGeneration(g => g + 1)
            fetchPriceStatus()
            return
          } else {
            // Nothing is running, and the run on file is not ours to report.
            // On a refused start that means the refusal was not a running
            // sync after all -- POST /collection/refresh answers 409 for any
            // reason start_sync declines, a Plex match for this user included.
            if (idleMessage) {
              setSyncStatus(idleMessage)
              // ...and refetch regardless. A sync on the other Machine can
              // finish between the 409 and this first read, and a run that
              // ended in that gap is indistinguishable here from one that
              // ended last week -- nothing on the row says which, because the
              // refusal never named the run that caused it. Being wrong about
              // *why* a click was refused costs a banner line; being wrong
              // about the data leaves the table showing the library from
              // before a sync that has just finished, which is precisely the
              // failure this change exists to remove. So the cheap half of the
              // trade is taken every time: one collection read, which on the
              // ordinary refusal (a Plex match, nothing having finished) is
              // all it costs.
              setSyncGeneration(g => g + 1)
            }
            return
          }
        }
        await new Promise(r => setTimeout(r, COLLECTION_SYNC_POLL_MS))
      }
    }
    poll()
    return () => { cancelled = true }
    // Keyed on whether the user is signed in, not on the authState object:
    // that object is replaced on every revalidation, and restarting the poll
    // for one costs nothing but risks everything above.
  }, [authed, syncPollNonce, setSyncStatus, fetchPriceStatus, releaseSyncFollow])

  // Follows a run to its end over HTTP, because the events that narrate one
  // reach only the Machine running it. Keyed on the flag rather than on the
  // run object, so a poll that finds the run still going does not restart its
  // own effect; it stops the moment a read says the run has ended, and the
  // next start (or a stock_judgment_started that did reach this browser)
  // starts it again.
  useEffect(() => {
    if (!authed || !recommendationRunning) return
    let cancelled = false
    async function poll() {
      while (!cancelled) {
        await new Promise(r => setTimeout(r, JUDGMENT_RUN_POLL_MS))
        if (cancelled) return
        const status = await refreshJudgmentStatus()
        if (cancelled) return
        // A failed read says nothing, so it is not taken as an ending: the run
        // is spending the user's Anthropic key and the Stop button has to stay
        // reachable through a dropped request. Unlike the collection poll's
        // bounded retries, there is no refused-start case here to give up on.
        if (status && !status.run?.running) return
      }
    }
    poll()
    return () => { cancelled = true }
  }, [authed, recommendationRunning, refreshJudgmentStatus])

  const followSyncRun = useCallback((intent: { adoptTerminal: boolean; idleMessage: string | null }) => {
    syncPollIntentRef.current = intent
    setSyncPollNonce(n => n + 1)
  }, [])

  const startRefresh = useCallback(async (mode: 'all' | 'new') => {
    setCollectionStatus(null)
    try {
      await refreshCollection(mode)
    } catch (e: any) {
      // 409 is a refused start, not a failed sync, and says no more than
      // that: the server answers it for every reason start_sync declines --
      // a sync already running (this tab's own earlier click, another tab's,
      // or the other Machine's, which this tab could not have heard start) or
      // a Plex match for this user. Which it was is what the poll below goes
      // and reads; "Sync failed" would answer neither.
      if (e?.status !== 409) {
        setSyncStatus(`Sync failed: ${e.message}`)
        return
      }
      followSyncRun({ adoptTerminal: false, idleMessage: REFUSED_START_MESSAGE })
      return
    }
    followSyncRun({ adoptTerminal: true, idleMessage: null })
  }, [setSyncStatus, followSyncRun])

  const handleRefresh = useCallback(async (mode?: 'all' | 'new') => {
    if (mode) {
      startRefresh(mode)
      return
    }
    try {
      const status = await getCollectionStatus()
      // A sync is already under way -- this tab's, another tab's, or the other
      // Machine's. Neither of the modal's choices could start anything, so
      // show what is running instead of asking a question already answered.
      if (status.sync?.running) {
        followSyncRun({ adoptTerminal: true, idleMessage: null })
        return
      }
      if (status.total > 0) {
        setCollectionStatus(status)
        return
      }
    } catch {
      // fall through to full refresh
    }
    startRefresh('all')
  }, [startRefresh, followSyncRun])

  // Wantlist tab's refresh has nothing analogous to the "N records already
  // loaded, refresh new or all?" choice that collectionStatus's modal offers --
  // wantlists are small and always fully re-synced -- so this skips straight
  // to the sync, same as Settings' "Refresh Now" bypassing that modal.
  const handleRefreshWantlist = useCallback(async () => {
    try {
      await refreshCollection('all', 'wantlist')
    } catch (e: any) {
      if (e?.status !== 409) {
        setSyncStatus(`Sync failed: ${e.message}`)
        return
      }
      followSyncRun({ adoptTerminal: false, idleMessage: REFUSED_START_MESSAGE })
      return
    }
    followSyncRun({ adoptTerminal: true, idleMessage: null })
  }, [setSyncStatus, followSyncRun])

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
    const statusSeq = ++latestStatusOwnerSeq.current
    const ownsStatus = () => statusSeq === latestStatusOwnerSeq.current
    setCheckpointStatus(null)
    setPriceRefreshStarting(true)
    const starting = releaseId
      ? 'Starting price refresh for this record…'
      : mode === 'missing'
        ? 'Starting price refresh for records with no price yet…'
        : 'Starting price refresh for every record…'
    setBusyStatusMessage(starting)
    setSyncStatus(starting)
    // Recorded after its own write, so the claim is measuring silence from the
    // point its message landed rather than counting that message as news.
    priceClaimNotice.current = {
      writes: statusWrites.current,
      lost: 'Lost track of the price refresh — check the Logs tab to see whether it started.',
    }
    try {
      const { enqueued } = await postCrawlStart(mode ?? 'all', releaseId)
      if (seq !== latestPriceRefreshSeq.current || !ownsStatus()) return
      setSyncStatus(enqueued === 0
        ? (mode === 'missing'
          ? 'Nothing to refresh — every record already has a price.'
          : 'Nothing to refresh — no records matched.')
        : `Price refresh requested for ${enqueued} ${enqueued === 1 ? 'record' : 'records'}.`)
    } catch (e: any) {
      if (seq !== latestPriceRefreshSeq.current || !ownsStatus()) return
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
  // feedback is the same either way: claim the button now, and only hand back
  // to the real stock_sync_* state once the server has answered. The claim is
  // the whole of the confirmation -- the button it was clicked with spins and
  // its row lights up, so a banner line saying the same thing was redundant
  // from the moment it appeared until stock_sync_started replaced it. The
  // banner still carries what the button cannot: a rejection, a failed
  // request, and the real progress that follows.
  const startStockSync = useCallback(async (crawlerId?: number) => {
    const seq = ++latestStockSyncStartSeq.current
    const statusSeq = latestStatusOwnerSeq.current
    const ownsStatus = () => statusSeq === latestStatusOwnerSeq.current
    setStockSyncStarting(crawlerId ?? 'all')
    try {
      const result = await postStockSyncStart(crawlerId)
      if (seq !== latestStockSyncStartSeq.current) return
      // On an accepted start the optimistic state stays until
      // stock_sync_started replaces it; a rejection ends here.
      if (!result.started) setStockSyncStarting(null)
      // busyStatusMessage is left alone throughout: this handler no longer
      // writes the banner on the way in, so anything set there belongs to
      // another request and clearing it would stop that one's spinner.
      if (ownsStatus()) reportStockSyncRejection(result, setSyncStatus)
    } catch (e: any) {
      if (seq !== latestStockSyncStartSeq.current) return
      setStockSyncStarting(null)
      if (ownsStatus()) setSyncStatus(`In-stock sync failed to start: ${e.message}`)
    }
  }, [setSyncStatus])

  const handleRefreshStock = useCallback(() => startStockSync(), [startStockSync])

  const handleRefreshStoreCrawler = useCallback(
    (crawlerId: number) => startStockSync(crawlerId),
    [startStockSync],
  )

  const handleRefreshRecommendations = useCallback(async () => {
    // Claimed before the request goes out, not after it comes back. The worker
    // can broadcast stock_judgment_started, and the user can click Stop, while
    // this POST is still in flight -- and this reply's snapshot predates both,
    // so advancing the sequence on arrival would make a stale answer the
    // newest writer and turn a disabled "Stopping…" back into "Stop".
    const action = ++latestJudgmentActionSeq.current
    const seq = ++latestJudgmentRunSeq.current
    try {
      const r = await postJudgmentStart()
      if (action !== latestJudgmentActionSeq.current) return
      if (seq !== latestJudgmentRunSeq.current) return
      // The reply carries the row, so the button flips to Stop on the response
      // alone. Waiting for stock_judgment_started would leave it reading
      // Refresh for the whole of a run whose events went to the other Machine.
      setRecommendationRunning(r.running)
      setRecommendationStopping(Boolean(r.run?.running && r.run.stop_requested))
      // A refused start used to pass in silence, which is the same
      // "did that do anything?" the button's own faces exist to answer.
      if (!r.started && r.running) {
        setSyncStatus('A recommendation run is already under way — use Stop to end it.')
      }
    } catch (e: any) {
      // Fenced exactly like the success path above. A rejection can arrive
      // after a started event has enabled Stop and the user has clicked it, and
      // both the message and the recovery read below would then talk over that
      // newer state -- the read especially, since its snapshot can predate the
      // stop commit and would turn the disabled "Stopping…" back into "Stop".
      if (action !== latestJudgmentActionSeq.current) return
      // A failed request is not a failed start. The server may have claimed
      // the run and created the task before the connection dropped, in which
      // case a run is under way, spending the user's key, with nothing here
      // polling it and a button still reading Refresh. So the row is asked
      // instead of assumed -- and the verdict waits for that answer rather
      // than announcing a failure the recovery may be about to contradict,
      // which would leave "failed to start" on screen beside a live Stop
      // button.
      setSyncStatus('Checking whether the recommendation run started…')
      const running = await discoverJudgmentRun(true)
      // Against the action counter, not the read counter: the recovery read
      // above bumps the latter itself, so comparing against it here would
      // discard this verdict every time.
      if (action !== latestJudgmentActionSeq.current) return
      setSyncStatus(
        running
          ? 'That request failed, but the recommendation run did start — use Stop to end it.'
          : `Refresh recommendations failed to start: ${e.message}`,
      )
    }
  }, [setSyncStatus, discoverJudgmentRun])

  const handleStopRecommendations = useCallback(async () => {
    // Optimistic, and corrected by the reply a moment later: the click has to
    // change the button now, or it reads as ignored for as long as the request
    // takes. Claimed first so anything already in flight -- a poll that read
    // the row before the flag was written, a start response whose snapshot
    // predates this click -- cannot land on top of it and flick the button
    // back to Stop, and claimed again on arrival for the same reason.
    const action = ++latestJudgmentActionSeq.current
    latestJudgmentRunSeq.current++
    judgmentStopPending.current = true
    setRecommendationStopping(true)
    try {
      const r = await postJudgmentStop()
      if (action !== latestJudgmentActionSeq.current) return
      // No read-sequence check here: a poll tick during this request bumps that
      // counter, and checking it would discard the one answer that actually
      // knows whether the stop landed. The bump instead, so a read still in
      // flight is superseded by this reply rather than the other way round.
      latestJudgmentRunSeq.current++
      setRecommendationRunning(Boolean(r.run?.running))
      setRecommendationStopping(Boolean(r.run?.running && r.run.stop_requested))
      // Three outcomes, not two. A refused stop is usually a run that finished
      // in the moment before the click -- but it is also how a run whose
      // Machine died since the last poll answers, and that one did not finish,
      // it stopped responding. Saying so names the state the staleness window
      // exists to recover from, rather than reporting a completion that never
      // happened.
      setSyncStatus(
        r.stopping
          ? 'Stopping the recommendation run — finishing the batch already paid for…'
          : r.run?.stale
            ? 'That recommendation run stopped responding — nothing is running now, and Refresh will start a fresh one.'
            : 'No recommendation run to stop — it had already finished.',
      )
    } catch (e: any) {
      if (action !== latestJudgmentActionSeq.current) return
      latestJudgmentRunSeq.current++
      setRecommendationStopping(false)
      setSyncStatus(`Stop recommendations failed: ${e.message}`)
    } finally {
      // Only the newest stop releases the suppression; an older one losing the
      // race must not re-open the window its successor is still inside.
      if (action === latestJudgmentActionSeq.current) judgmentStopPending.current = false
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

  useEffect(() => {
    if (!isMobile) setAdminMenuOpen(false)
  }, [isMobile])

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
    return <div className="min-h-dvh flex items-center justify-center text-gray-500">Loading…</div>
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

  const bannerShellClass = isMobile
    ? 'shrink-0 bg-gray-900 border-t border-gray-700 px-safe'
    : 'fixed bottom-0 left-0 right-0 bg-gray-900 border-t border-gray-700 px-safe pb-safe'

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
    <div className="h-dvh bg-gray-950 text-gray-100 flex flex-col overflow-hidden">
      {/* Wrapper is `inert` while the backend is confirmed down, or while a
          post-recovery session revalidation is still in flight, so a
          keyboard or screen-reader user can't tab into the frozen app
          underneath the BackendDownScreen overlay. `display: contents` keeps
          it invisible to layout -- header/main/etc. stay direct flex
          children of the h-dvh container above. */}
      <div inert={backendUp === false || authRevalidating} className="contents">
      {/* Header. The safe-area insets sit on the <header> and the padding on the
          row inside it, so the two compose instead of one overriding the other
          -- the bar's background still paints under the notch. */}
      <header className="bg-gray-900 border-b border-gray-800 pt-safe px-safe">
        <div className="flex items-center gap-3 px-4 py-3 md:gap-4 md:px-6">
          {isMobile ? (
            <span className="text-sm font-semibold text-gray-200">Track Tempest</span>
          ) : (
            <nav className="flex gap-2">
              {LIBRARY_TABS.map((tab) => (
                <button
                  key={tab.view}
                  onClick={() => setView(tab.view)}
                  className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === tab.view)}`}
                >
                  {tab.label}
                </button>
              ))}
            </nav>
          )}
          <nav className="flex items-center gap-1 ml-auto md:gap-2">
            {showAdminNav && (isMobile ? (
              <button
                onClick={() => setAdminMenuOpen(true)}
                aria-label="More"
                aria-expanded={adminMenuOpen}
                className={`w-11 h-11 flex items-center justify-center ${
                  navButtonClass(adminMenuOpen || ADMIN_TABS.some((tab) => tab.view === view))
                }`}
              >
                <span aria-hidden="true" className="text-lg leading-none">⋯</span>
              </button>
            ) : (
              ADMIN_TABS.map((tab) => (
                <button
                  key={tab.view}
                  onClick={() => setView(tab.view)}
                  className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === tab.view)}`}
                >
                  {tab.label}
                </button>
              ))
            ))}
            <NotificationBell
              unread={unreadNotifications}
              active={view === 'notifications'}
              onClick={() => setView('notifications')}
            />
            <button
              onClick={() => setView('account')}
              aria-label="Profile"
              className={`w-11 h-11 md:w-8 md:h-8 rounded-full overflow-hidden flex items-center justify-center transition-colors ${
                view === 'account' ? 'ring-2 ring-white' : 'hover:ring-2 hover:ring-gray-600'
              }`}
            >
              <Avatar version={avatarVersion} size="sm" />
            </button>
          </nav>
        </div>
      </header>

      {/* Main */}
      <main className="flex-1 overflow-hidden px-safe md:pb-safe">
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
          <StockBrowser recommendedAvailable={recommendedAvailable} hiddenCrawlerIds={hiddenCrawlerIds} crawlers={crawlers} onHiddenCrawlerIdsChange={updateHiddenCrawlerIds} hiddenCrawlerIdsLoaded={hiddenCrawlerIdsLoaded} syncGeneration={stockSyncGeneration} inventoryGeneration={stockInventoryGeneration} judgmentGeneration={stockJudgmentGeneration} libraryGeneration={syncGeneration} isAdmin={showAdminNav} hasPriceField={hasPriceData} />
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
            onStopRecommendations={handleStopRecommendations}
            recommendationRunning={recommendationRunning}
            recommendationStopping={recommendationStopping}
            onExportRecommendations={handleExportRecommendations}
            onImportRecommendations={handleImportRecommendations}
            onClearRecommendations={handleClearRecommendations}
            hasJudgedItems={hasJudgedItems}
          />
        </div>
        {/* Mounted only while it is the active view, not hidden like the tabs
            above: loading this view is what marks its notifications read, so a
            permanently-mounted copy would clear the bell's dot on app start,
            before the user had seen anything. */}
        {view === 'notifications' && (
          <div className="h-full overflow-y-auto">
            <Notifications generation={priceGeneration} onLoaded={handleNotificationsLoaded} />
          </div>
        )}
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

      {/* Collection sync status bar. The live region stays mounted even when
          the banner is not: assistive technology does not reliably announce a
          role="status" element inserted together with its text, so the first
          confirmation after a click would be the one that went unheard. */}
      <div role="status" className="shrink-0">
      {syncBannerVisible && (
        <div className={bannerShellClass}>
          <div className="px-4 py-2 flex flex-wrap items-center gap-x-3 gap-y-1 md:flex-nowrap">
            <span className="text-sm font-medium text-gray-300 md:shrink-0">
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
        </div>
      )}
      </div>

      {/* Crawl status bar */}
      {crawlBannerVisible && !syncBannerVisible && (
        <div className={bannerShellClass}>
          <div className="px-4 py-2 flex flex-wrap items-center gap-x-3 gap-y-1 md:flex-nowrap">
            <span className="text-sm font-medium text-gray-300 md:shrink-0">
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
        </div>
      )}

      {/* Mobile tab bar. Rendered instead of the header's library nav, never
          alongside it: a second set of buttons named Collection/Wantlist/...
          would land in the accessibility tree twice. */}
      {isMobile && (
        <BottomNav
          tabs={LIBRARY_TABS}
          active={view as LibraryView}
          onSelect={(next) => setView(next)}
        />
      )}

      {/* Admin overflow menu */}
      {isMobile && showAdminNav && (
        <Sheet open={adminMenuOpen} onClose={() => setAdminMenuOpen(false)} label="Admin sections">
          <div className="flex flex-col p-2 pb-4">
            {ADMIN_TABS.map((tab) => (
              <button
                key={tab.view}
                onClick={() => { setView(tab.view); setAdminMenuOpen(false) }}
                className={`px-4 py-3 text-left text-base font-medium ${navButtonClass(view === tab.view)}`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </Sheet>
      )}

      {/* Collection refresh modal */}
      {collectionStatus && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-900 border border-gray-700 rounded-xl shadow-xl p-6 w-full max-w-sm">
            <h2 className="text-white font-semibold text-lg mb-2">Collection already loaded</h2>
            <p className="text-gray-400 text-sm mb-1">
              <span className="text-white font-medium">{collectionStatus.total}</span> records in your collection.
            </p>
            {collectionStatus.last_synced && (
              <p className="text-gray-500 text-xs mb-5">
                Last synced: {new Date(collectionStatus.last_synced).toLocaleString()}
              </p>
            )}
            <div className="flex flex-col gap-3 md:flex-row">
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
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-900 border border-gray-700 rounded-xl shadow-xl p-6 w-full max-w-sm">
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
            <div className="flex flex-col gap-3 md:flex-row">
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
