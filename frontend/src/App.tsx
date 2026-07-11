import { useState, useEffect } from 'react'
import RecordBrowser from './views/RecordBrowser'
import StockBrowser from './views/StockBrowser'
import Settings from './views/Settings'
import LogViewer from './views/LogViewer'
import LoginScreen from './views/LoginScreen'
import SetupWizard from './views/SetupWizard'
import { refreshCollection, getCollectionStatus, openCrawlStream, getCrawlStatus, postCrawlStart, postStockSyncStart, postJudgmentStart, clearJudgments, exportRecommendationsCsv, getCrawlers, getSettings, getJudgmentStatus, checkHealth, getAuthState, setUnauthorizedHandler } from './api/client'
import type { CrawlEvent, CrawlStatus, CollectionStatus, Crawler, AuthState } from './api/types'

type View = 'collection' | 'wishlist' | 'instock' | 'settings' | 'logs'

export default function App() {
  const [view, setView] = useState<View>('collection')
  const [crawlEvents, setCrawlEvents] = useState<CrawlEvent[]>([])
  const [crawling, setCrawling] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [crawlCurrent, setCrawlCurrent] = useState<CrawlEvent | null>(null)
  const [crawlCount, setCrawlCount] = useState(0)
  const [crawlTotal, setCrawlTotal] = useState(0)
  const [checkpointStatus, setCheckpointStatus] = useState<CrawlStatus | null>(null)

  const [collectionStatus, setCollectionStatus] = useState<CollectionStatus | null>(null)
  const [crawlingReleaseId, setCrawlingReleaseId] = useState<string | undefined>(undefined)
  const [crawlers, setCrawlers] = useState<Crawler[]>([])
  const [hasAnthropicKey, setHasAnthropicKey] = useState(false)
  const [hasPlexConfigured, setHasPlexConfigured] = useState(false)
  const [hasJudgedItems, setHasJudgedItems] = useState(false)
  const [judgmentRunning, setJudgmentRunning] = useState(false)
  const [serverReady, setServerReady] = useState(false)
  const [syncMessage, setSyncMessage] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [authState, setAuthState] = useState<AuthState | null>(null)

  // Poll /api/health until the backend is up, then load initial data.
  useEffect(() => {
    if (authState !== 'authenticated') return
    let cancelled = false
    async function poll() {
      while (!cancelled) {
        const ok = await checkHealth()
        if (ok) {
          if (!cancelled) {
            setServerReady(true)
            getCrawlers().then(setCrawlers).catch(() => {})
            getSettings().then((s) => {
              setHasAnthropicKey(Boolean(s.anthropic_api_key))
              setHasPlexConfigured(Boolean(s.plex_base_url && s.plex_token))
            }).catch(() => {})
            getJudgmentStatus().then((s) => setHasJudgedItems(s.any_judged)).catch(() => {})
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
    if (authState !== 'authenticated') return
    let source: EventSource | null = null
    let reconnectTimer: ReturnType<typeof setTimeout>
    let destroyed = false

    function handleEvent(e: MessageEvent) {
      const event: CrawlEvent = JSON.parse(e.data)
      if (event.status === 'ping') return
      if (event.status === 'sync_started') {
        setSyncing(true)
        setSyncMessage('Syncing collection…')
        return
      }
      if (event.status === 'sync_progress') {
        setSyncMessage(`Syncing collection… ${event.synced} records (page ${event.page}/${event.total_pages})`)
        return
      }
      if (event.status === 'sync_complete') {
        setSyncing(false)
        const wishlistPart = event.wishlist_synced != null ? `, ${event.wishlist_synced} wishlist items` : ''
        setSyncMessage(`Synced ${event.synced} records for ${event.username}${wishlistPart}`)
        return
      }
      if (event.status === 'sync_error') {
        setSyncing(false)
        setSyncMessage(`Sync failed: ${event.error}`)
        return
      }
      if (event.status === 'plex_match_started') {
        setSyncMessage('Matching collection against Plex…')
        return
      }
      if (event.status === 'plex_match_progress') {
        setSyncMessage(`Matching collection against Plex… ${event.matched}/${event.total}`)
        return
      }
      if (event.status === 'plex_match_complete') {
        setSyncMessage(`Plex match complete — ${event.matched} matched`)
        return
      }
      if (event.status === 'plex_match_error') {
        setSyncMessage(`Plex match failed: ${event.error}`)
        return
      }
      if (event.status === 'stock_sync_started') {
        setSyncing(true)
        setSyncMessage('Syncing in-stock catalog…')
        return
      }
      if (event.status === 'stock_sync_progress') {
        setSyncMessage(`Syncing in-stock catalog… ${event.synced} items (${event.source})`)
        return
      }
      if (event.status === 'stock_sync_complete') {
        setSyncing(false)
        setSyncMessage(`In-stock sync complete: ${event.synced} items`)
        return
      }
      if (event.status === 'stock_sync_error') {
        setSyncing(false)
        setSyncMessage(`In-stock sync failed: ${event.error}`)
        return
      }
      if (event.status === 'stock_judgment_started') {
        setSyncing(true)
        setJudgmentRunning(true)
        setSyncMessage('Finding recommendations for Store items…')
        return
      }
      if (event.status === 'stock_judgment_progress') {
        setSyncMessage(`Finding recommendations for Store items… ${event.judged}/${event.total}`)
        return
      }
      if (event.status === 'stock_judgment_complete') {
        setSyncing(false)
        setJudgmentRunning(false)
        setHasJudgedItems(true)
        setSyncMessage(`Finished finding recommendations — ${event.judged} items checked`)
        return
      }
      if (event.status === 'stock_judgment_error') {
        setSyncing(false)
        setJudgmentRunning(false)
        setSyncMessage(`Finding recommendations failed: ${event.error}`)
        return
      }
      if (event.status === 'started') {
        setCrawlTotal(event.total ?? 0)
        setCrawling(true)
        setDrawerOpen(true)
        setCrawlEvents([])
        setCrawlCount(0)
        setCrawlCurrent(null)
      } else if (event.status === 'complete' || event.status === 'stopped') {
        setCrawling(false)
        setCrawlCurrent(null)
        setCrawlingReleaseId(undefined)
      } else if (event.status === 'error' && !event.release) {
        setCrawling(false)
      } else if (event.release) {
        setCrawlCurrent(event)
        setCrawlCount((n) => n + 1)
        setCrawlEvents((prev) => [...prev, event])
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
  }, [authState])

  useEffect(() => {
    setUnauthorizedHandler(() => setAuthState('unauthenticated'))
    getAuthState().then(setAuthState).catch(() => setAuthState('unauthenticated'))
  }, [])

  async function handleRefresh(mode?: 'all' | 'new') {
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
  }

  async function startRefresh(mode: 'all' | 'new') {
    setCollectionStatus(null)
    try {
      await refreshCollection(mode)
    } catch (e: any) {
      setSyncMessage(`Sync failed: ${e.message}`)
    }
  }

  async function handleFindPrices(releaseId?: string, mode?: 'all' | 'missing') {
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
  }

  function startCrawl(releaseId?: string, mode?: 'all' | 'missing') {
    setCheckpointStatus(null)
    setCrawlingReleaseId(releaseId)
    postCrawlStart(mode ?? 'all', releaseId).catch((e: any) => {
      alert(`Failed to start crawl: ${e.message}`)
    })
  }

  async function handleRefreshStock() {
    try {
      await postStockSyncStart()
    } catch (e: any) {
      setSyncMessage(`In-stock sync failed to start: ${e.message}`)
    }
  }

  async function handleRefreshRecommendations() {
    try {
      await postJudgmentStart()
    } catch (e: any) {
      setSyncMessage(`Refresh recommendations failed to start: ${e.message}`)
    }
  }

  async function handleExportRecommendations() {
    try {
      const blob = await exportRecommendationsCsv()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'recommendations.csv'
      a.click()
      URL.revokeObjectURL(url)
    } catch (e: any) {
      setSyncMessage(`Export recommendations failed: ${e.message}`)
    }
  }

  async function handleClearRecommendations() {
    if (!window.confirm('Clear all recommendations? This removes every recommended and not-recommended judgment from the database — every Store item will need to be re-evaluated from scratch, which costs Anthropic API calls to redo.')) {
      return
    }
    try {
      const result = await clearJudgments()
      if (!result.cleared) {
        setSyncMessage('Cannot clear recommendations while a sync or recommendation run is in progress')
        return
      }
      setHasJudgedItems(false)
      setSyncMessage(`Cleared ${result.count} recommendation judgments`)
    } catch (e: any) {
      setSyncMessage(`Clear recommendations failed: ${e.message}`)
    }
  }

  if (authState === null) {
    return <div className="min-h-screen flex items-center justify-center text-gray-500">Loading…</div>
  }
  if (authState === 'setup_required') {
    return <SetupWizard onComplete={() => setAuthState('authenticated')} />
  }
  if (authState === 'unauthenticated') {
    return <LoginScreen onAuthenticated={() => setAuthState('authenticated')} />
  }

  const recommendedAvailable = hasAnthropicKey && hasJudgedItems && !judgmentRunning

  return (
    <div className="h-screen bg-gray-950 text-gray-100 flex flex-col overflow-hidden">
      {/* Header */}
      <header className="bg-gray-900 border-b border-gray-800 px-6 py-3 flex items-center gap-4">
        <nav className="flex gap-2">
          <button
            onClick={() => setView('collection')}
            className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
              view === 'collection'
                ? 'bg-indigo-600 text-white'
                : 'text-gray-400 hover:text-white'
            }`}
          >
            Collection
          </button>
          <button
            onClick={() => setView('wishlist')}
            className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
              view === 'wishlist'
                ? 'bg-indigo-600 text-white'
                : 'text-gray-400 hover:text-white'
            }`}
          >
            Wishlist
          </button>
          <button
            onClick={() => setView('instock')}
            className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
              view === 'instock'
                ? 'bg-indigo-600 text-white'
                : 'text-gray-400 hover:text-white'
            }`}
          >
            Store
          </button>
        </nav>
        <nav className="flex gap-2 ml-auto">
          <button
            onClick={() => setView('settings')}
            className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
              view === 'settings'
                ? 'bg-indigo-600 text-white'
                : 'text-gray-400 hover:text-white'
            }`}
          >
            Settings
          </button>
          <button
            onClick={() => setView('logs')}
            className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
              view === 'logs'
                ? 'bg-indigo-600 text-white'
                : 'text-gray-400 hover:text-white'
            }`}
          >
            Logs
          </button>
        </nav>
      </header>

      {/* Main */}
      <main className="flex-1 overflow-hidden">
        <div className={view === 'collection' ? 'h-full' : 'hidden'}>
          <RecordBrowser
            scope="collection"
            onRefreshPrices={(id) => handleFindPrices(id)}
            crawling={crawling}
            crawlingReleaseId={crawlingReleaseId}
            crawlEvents={crawlEvents}
            crawlers={crawlers}
            syncing={syncing}
            plexAvailable={hasPlexConfigured}
          />
        </div>
        <div className={view === 'wishlist' ? 'h-full' : 'hidden'}>
          <RecordBrowser
            scope="wishlist"
            onRefreshPrices={(id) => handleFindPrices(id)}
            crawling={crawling}
            crawlingReleaseId={crawlingReleaseId}
            crawlEvents={crawlEvents}
            crawlers={crawlers}
            syncing={syncing}
            plexAvailable={hasPlexConfigured}
          />
        </div>
        <div className={view === 'instock' ? 'h-full' : 'hidden'}>
          <StockBrowser recommendedAvailable={recommendedAvailable} />
        </div>
        <div className={view === 'settings' ? 'h-full overflow-y-auto' : 'hidden'}><Settings crawlers={crawlers} onCrawlersChange={setCrawlers} onRefreshCollection={(mode) => handleRefresh(mode)} onRefreshPrices={(mode) => handleFindPrices(undefined, mode)} onRefreshStock={handleRefreshStock} onRefreshRecommendations={handleRefreshRecommendations} onExportRecommendations={handleExportRecommendations} onClearRecommendations={handleClearRecommendations} hasJudgedItems={hasJudgedItems} /></div>
        <div className={view === 'logs' ? 'h-full' : 'hidden'}><LogViewer /></div>
      </main>

      {/* Collection refresh modal */}
      {collectionStatus && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-gray-900 border border-gray-700 rounded-lg shadow-xl p-6 w-96 max-w-full mx-4">
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
                className="flex-1 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-sm font-medium transition-colors"
              >
                Refresh New Only
                <span className="block text-xs font-normal text-indigo-300">Skip existing records</span>
              </button>
              <button
                onClick={() => startRefresh('all')}
                className="flex-1 px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded text-sm font-medium transition-colors"
              >
                Refresh All
                <span className="block text-xs font-normal text-gray-400">Re-sync {collectionStatus.total} records</span>
              </button>
            </div>
            <button
              onClick={() => setCollectionStatus(null)}
              className="mt-3 w-full text-gray-500 hover:text-gray-300 text-sm transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Checkpoint modal */}
      {checkpointStatus && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-gray-900 border border-gray-700 rounded-lg shadow-xl p-6 w-96 max-w-full mx-4">
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
                className="flex-1 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-sm font-medium transition-colors"
              >
                Resume
                <span className="block text-xs font-normal text-indigo-300">{checkpointStatus.missing} records</span>
              </button>
              <button
                onClick={() => startCrawl(undefined, 'all')}
                className="flex-1 px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded text-sm font-medium transition-colors"
              >
                Restart
                <span className="block text-xs font-normal text-gray-400">{checkpointStatus.total} records</span>
              </button>
            </div>
            <button
              onClick={() => setCheckpointStatus(null)}
              className="mt-3 w-full text-gray-500 hover:text-gray-300 text-sm transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Server startup overlay */}
      {!serverReady && (
        <div className="fixed inset-0 bg-gray-950/90 flex flex-col items-center justify-center z-50 gap-4">
          <div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {/* Collection sync status bar */}
      {syncMessage && (
        <div className="fixed bottom-0 left-0 right-0 bg-gray-900 border-t border-gray-700 px-4 py-2 flex items-center gap-3">
          <span className="text-sm font-medium text-gray-300 shrink-0">
            {syncMessage}
          </span>
          {syncing && (
            <div className="w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin shrink-0" />
          )}
          {!syncing && (
            <button
              onClick={() => setSyncMessage(null)}
              className="ml-auto text-gray-400 hover:text-white text-sm shrink-0"
            >
              Dismiss
            </button>
          )}
        </div>
      )}

      {/* Crawl status bar */}
      {drawerOpen && !syncMessage && (
        <div className="fixed bottom-0 left-0 right-0 bg-gray-900 border-t border-gray-700 px-4 py-2 flex items-center gap-3">
          <span className="text-sm font-medium text-gray-300 shrink-0">
            {crawling ? 'Refreshing prices…' : 'Done'}
          </span>
          {crawling && crawlCurrent && (
            <span className="text-sm text-gray-400 truncate">
              {crawlTotal > 0 ? `${crawlCount}/${crawlTotal}: ` : ''}
              <span className="text-gray-200">{crawlCurrent.artist} — {crawlCurrent.release}</span>
              {' '}on{' '}
              <span className="text-indigo-400">{crawlCurrent.site}</span>
            </span>
          )}
          {!crawling && (
            <button
              onClick={() => setDrawerOpen(false)}
              className="ml-auto text-gray-400 hover:text-white text-sm shrink-0"
            >
              Dismiss
            </button>
          )}
        </div>
      )}
    </div>
  )
}
