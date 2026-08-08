import { useState, useEffect, useCallback } from 'react'
import RecordBrowser from './views/RecordBrowser'
import StockBrowser from './views/StockBrowser'
import Settings from './views/Settings'
import Account from './views/Account'
import LogViewer from './views/LogViewer'
import LoginScreen from './views/LoginScreen'
import InviteCodeScreen from './views/InviteCodeScreen'
import Avatar from './components/Avatar'
import { navButtonClass, primaryButtonClass, secondaryButtonClass, dismissButtonClass } from './styles/buttons'
import { refreshCollection, getCollectionStatus, openCrawlStream, getCrawlStatus, postCrawlStart, postStockSyncStart, postJudgmentStart, clearJudgments, exportRecommendationsCsv, getCrawlers, getUserSettings, getJudgmentStatus, checkHealth, getAuthStatus, setUnauthorizedHandler, hasAvatar } from './api/client'
import type { CrawlEvent, CrawlStatus, CollectionStatus, Crawler, AuthStatus } from './api/types'

type View = 'discogs' | 'wishlist' | 'instock' | 'settings' | 'logs' | 'account'

// SSE reconnects (including on browser refresh) replay every buffered event from
// crawl_manager._recent, so a banner's dismissal has to survive across that replay.
// Each broadcast event carries a monotonic `id`; we persist the id of the last-dismissed
// event and only show a banner when the current event's id is newer than that.
const DISMISSED_SYNC_KEY = 'discogs-browser.dismissedSyncEventId'
const DISMISSED_CRAWL_KEY = 'discogs-browser.dismissedCrawlEventId'
const VIEW_AS_USER_KEY = 'discogs-browser.viewAsUser'
const HIDDEN_CRAWLER_IDS_KEY = 'discogs-browser.hiddenCrawlerIds'

export default function App() {
  const [view, setView] = useState<View>('discogs')
  const [crawling, setCrawling] = useState(false)
  const [crawlBannerId, setCrawlBannerId] = useState(0)
  const [dismissedCrawlId, setDismissedCrawlId] = useState(() => Number(localStorage.getItem(DISMISSED_CRAWL_KEY) ?? 0))
  const [crawlCurrent, setCrawlCurrent] = useState<CrawlEvent | null>(null)
  const [crawlCount, setCrawlCount] = useState(0)
  const [crawlTotal, setCrawlTotal] = useState(0)
  const [checkpointStatus, setCheckpointStatus] = useState<CrawlStatus | null>(null)

  const [collectionStatus, setCollectionStatus] = useState<CollectionStatus | null>(null)
  const [crawlers, setCrawlers] = useState<Crawler[]>([])
  const [hiddenCrawlerIds, setHiddenCrawlerIds] = useState<number[]>(() => {
    try {
      const parsed = JSON.parse(localStorage.getItem(HIDDEN_CRAWLER_IDS_KEY) ?? '[]')
      return Array.isArray(parsed) ? parsed.filter((n) => typeof n === 'number') : []
    } catch {
      return []
    }
  })
  const [avatarVersion, setAvatarVersion] = useState(0)
  const [hasAnthropicKey, setHasAnthropicKey] = useState(false)
  const [hasJudgedItems, setHasJudgedItems] = useState(false)
  const [judgmentRunning, setJudgmentRunning] = useState(false)
  const [serverReady, setServerReady] = useState(false)
  const [syncMessage, setSyncMessage] = useState<string | null>(null)
  const [syncMessageId, setSyncMessageId] = useState<number | null>(null)
  const [dismissedSyncId, setDismissedSyncId] = useState(() => Number(localStorage.getItem(DISMISSED_SYNC_KEY) ?? 0))
  const [syncing, setSyncing] = useState(false)
  const [syncGeneration, setSyncGeneration] = useState(0)
  const [stockSyncTarget, setStockSyncTarget] = useState<number | 'all' | null>(null)
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

  const toggleCrawlerView = useCallback((crawlerId: number) => {
    setHiddenCrawlerIds((current) =>
      current.includes(crawlerId)
        ? current.filter((id) => id !== crawlerId)
        : [...current, crawlerId]
    )
  }, [])

  useEffect(() => {
    localStorage.setItem(HIDDEN_CRAWLER_IDS_KEY, JSON.stringify(hiddenCrawlerIds))
  }, [hiddenCrawlerIds])

  // Poll /api/health until the backend is up, then load initial data.
  useEffect(() => {
    if (authState?.state !== 'authenticated') return
    let cancelled = false
    async function poll() {
      while (!cancelled) {
        const ok = await checkHealth()
        if (ok) {
          if (!cancelled) {
            setServerReady(true)
            getCrawlers().then(setCrawlers).catch(() => {})
            getUserSettings().then((s) => {
              setHasAnthropicKey(Boolean(s.anthropic_api_key))
            }).catch(() => {})
            getJudgmentStatus().then((s) => setHasJudgedItems(s.any_judged)).catch(() => {})
            hasAvatar().then((exists) => setAvatarVersion(exists ? Date.now() : 0)).catch(() => {})
          }
          return
        }
        await new Promise(r => setTimeout(r, 2000))
      }
    }
    poll()
    return () => { cancelled = true }
  }, [authState])

  // Persistent SSE connection — reconnects on error. Waits for server to be ready.
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
        setSyncStatus(event.scope === 'wishlist' ? 'Syncing wishlist…' : 'Syncing collection…', event.id ?? null)
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
          setSyncStatus(`Synced ${event.wishlist_synced} wishlist items for ${event.username}`, event.id ?? null)
        } else {
          const wishlistPart = event.wishlist_synced != null ? `, ${event.wishlist_synced} wishlist items` : ''
          setSyncStatus(`Synced ${event.synced} records for ${event.username}${wishlistPart}`, event.id ?? null)
        }
        setSyncGeneration(g => g + 1)
        return
      }
      if (event.status === 'sync_error') {
        setSyncing(false)
        setSyncStatus(`Sync failed: ${event.error}`, event.id ?? null)
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
        setSyncStatus('Syncing in-stock catalog…', event.id ?? null)
        return
      }
      if (event.status === 'stock_sync_progress') {
        setSyncStatus(`Syncing in-stock catalog… ${event.synced} items (${event.source})`, event.id ?? null)
        return
      }
      if (event.status === 'stock_sync_complete') {
        setSyncing(false)
        setStockSyncTarget(null)
        setSyncStatus(`In-stock sync complete: ${event.synced} items`, event.id ?? null)
        return
      }
      if (event.status === 'stock_sync_error') {
        if (!event.source) {
          setSyncing(false)
          setStockSyncTarget(null)
        }
        setSyncStatus(`In-stock sync failed: ${event.error}`, event.id ?? null)
        return
      }
      if (event.status === 'stock_sync_aborted') {
        setSyncing(false)
        setStockSyncTarget(null)
        const sources = event.sources?.length ? ` (${event.sources.join(', ')})` : ''
        setSyncStatus(`In-stock sync stopped: ${event.error}${sources}`, event.id ?? null)
        return
      }
      if (event.status === 'stock_judgment_started') {
        setSyncing(true)
        setJudgmentRunning(true)
        setSyncStatus('Finding recommendations for Store items…', event.id ?? null)
        return
      }
      if (event.status === 'stock_judgment_progress') {
        setSyncStatus(`Finding recommendations for Store items… ${event.judged}/${event.total}`, event.id ?? null)
        return
      }
      if (event.status === 'stock_judgment_complete') {
        setSyncing(false)
        setJudgmentRunning(false)
        setHasJudgedItems(true)
        setSyncStatus(`Finished finding recommendations — ${event.judged} items checked`, event.id ?? null)
        return
      }
      if (event.status === 'stock_judgment_error') {
        setSyncing(false)
        setJudgmentRunning(false)
        setSyncStatus(`Finding recommendations failed: ${event.error}`, event.id ?? null)
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
  }, [authState, setSyncStatus])

  useEffect(() => {
    setUnauthorizedHandler(() => setAuthState({ state: 'unauthenticated' }))
    getAuthStatus().then(setAuthState).catch(() => setAuthState({ state: 'unauthenticated' }))
  }, [])

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

  // Wishlist tab's refresh has nothing analogous to the "N records already
  // loaded, refresh new or all?" choice that collectionStatus's modal offers --
  // wantlists are small and always fully re-synced -- so this skips straight
  // to the sync, same as Settings' "Refresh Now" bypassing that modal.
  const handleRefreshWishlist = useCallback(async () => {
    try {
      await refreshCollection('all', 'wishlist')
    } catch (e: any) {
      setSyncStatus(`Sync failed: ${e.message}`)
    }
  }, [setSyncStatus])

  const startCrawl = useCallback((releaseId?: string, mode?: 'all' | 'missing') => {
    setCheckpointStatus(null)
    postCrawlStart(mode ?? 'all', releaseId).catch((e: any) => {
      alert(`Failed to start crawl: ${e.message}`)
    })
  }, [])

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

  const handleRefreshStock = useCallback(async () => {
    try {
      await postStockSyncStart()
    } catch (e: any) {
      setSyncStatus(`In-stock sync failed to start: ${e.message}`)
    }
  }, [setSyncStatus])

  const handleRefreshStoreCrawler = useCallback(async (crawlerId: number) => {
    try {
      await postStockSyncStart(crawlerId)
    } catch (e: any) {
      setSyncStatus(`In-stock sync failed to start: ${e.message}`)
    }
  }, [setSyncStatus])

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

  const recommendedAvailable = hasAnthropicKey && hasJudgedItems && !judgmentRunning
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
      {/* Header */}
      <header className="bg-gray-900 border-b border-gray-800 px-6 py-3 flex items-center gap-4">
        <nav className="flex gap-2">
          <button
            onClick={() => setView('discogs')}
            className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'discogs')}`}
          >
            Discogs
          </button>
          <button
            onClick={() => setView('wishlist')}
            className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'wishlist')}`}
          >
            Wishlist
          </button>
          <button
            onClick={() => setView('instock')}
            className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'instock')}`}
          >
            Store
          </button>
        </nav>
        <nav className="flex items-center gap-2 ml-auto">
          {showAdminNav && (
            <button
              onClick={() => setView('logs')}
              className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'logs')}`}
            >
              Logs
            </button>
          )}
          <button
            onClick={() => setView('settings')}
            className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(view === 'settings')}`}
          >
            Settings
          </button>
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
        <div className={view === 'discogs' ? 'h-full' : 'hidden'}>
          <RecordBrowser
            scope="discogs"
            syncing={syncing}
            onRefreshCollection={() => handleRefresh()}
            syncGeneration={syncGeneration}
          />
        </div>
        <div className={view === 'wishlist' ? 'h-full' : 'hidden'}>
          <RecordBrowser
            scope="wishlist"
            syncing={syncing}
            onRefreshCollection={() => handleRefreshWishlist()}
            syncGeneration={syncGeneration}
          />
        </div>
        <div className={view === 'instock' ? 'h-full' : 'hidden'}>
          <StockBrowser recommendedAvailable={recommendedAvailable} hiddenCrawlerIds={hiddenCrawlerIds} />
        </div>
        <div className={view === 'settings' ? 'h-full overflow-y-auto' : 'hidden'}>
          <Settings
            crawlers={crawlers}
            onCrawlersChange={setCrawlers}
            onRefreshPrices={handleRefreshPricesFromSettings}
            onRefreshStock={handleRefreshStock}
            isAdmin={showAdminNav}
            hiddenCrawlerIds={hiddenCrawlerIds}
            onToggleCrawlerView={toggleCrawlerView}
            stockSyncBusy={stockSyncTarget !== null}
            stockSyncCrawlerId={typeof stockSyncTarget === 'number' ? stockSyncTarget : null}
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
            onClearRecommendations={handleClearRecommendations}
            hasJudgedItems={hasJudgedItems}
          />
        </div>
        <div className={view === 'logs' ? 'h-full' : 'hidden'}><LogViewer /></div>
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
        <div className="fixed bottom-0 left-0 right-0 bg-gray-900 border-t border-gray-700 px-4 py-2 flex items-center gap-3">
          <span className="text-sm font-medium text-gray-300 shrink-0">
            {syncMessage}
          </span>
          {syncing && (
            <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin shrink-0" />
          )}
          {!syncing && (
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
  )
}
