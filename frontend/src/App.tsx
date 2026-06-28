import { useState, useEffect } from 'react'
import CollectionBrowser from './views/CollectionBrowser'
import Settings from './views/Settings'
import LogViewer from './views/LogViewer'
import { refreshCollection, getCollectionStatus, openCrawlStream, getCrawlStatus, postCrawlStart, getCrawlers } from './api/client'
import type { CrawlEvent, CrawlStatus, CollectionStatus, Crawler } from './api/types'

type View = 'collection' | 'settings' | 'logs'

export default function App() {
  const [view, setView] = useState<View>('collection')
  const [refreshing, setRefreshing] = useState(false)
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

  useEffect(() => { getCrawlers().then(setCrawlers) }, [])

  // Persistent SSE connection — reconnects on error.
  // Handles both user-triggered and scheduled crawls.
  useEffect(() => {
    let source: EventSource | null = null
    let reconnectTimer: ReturnType<typeof setTimeout>
    let destroyed = false

    function handleEvent(e: MessageEvent) {
      const event: CrawlEvent = JSON.parse(e.data)
      if (event.status === 'ping') return
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
  }, [])

  async function handleRefresh() {
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
    setRefreshing(true)
    try {
      const result = await refreshCollection(mode)
      alert(`Synced ${result.synced} records for ${result.username}`)
    } catch (e: any) {
      alert(`Refresh failed: ${e.message}`)
    } finally {
      setRefreshing(false)
    }
  }

  async function handleFindPrices(releaseId?: string) {
    if (releaseId) {
      startCrawl(releaseId, undefined)
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

  return (
    <div className="h-screen bg-gray-950 text-gray-100 flex flex-col overflow-hidden">
      {/* Header */}
      <header className="bg-gray-900 border-b border-gray-800 px-6 py-3 flex items-center gap-4">
<nav className="flex gap-2 flex-1">
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
        <div className="flex gap-2">
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 disabled:opacity-50 rounded text-sm font-medium transition-colors"
          >
            {refreshing ? 'Refreshing…' : 'Refresh Collection'}
          </button>
          <button
            onClick={() => handleFindPrices()}
            disabled={crawling}
            className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 rounded text-sm font-medium transition-colors"
          >
            {crawling ? 'Refreshing…' : 'Refresh Prices'}
          </button>
        </div>
      </header>

      {/* Main */}
      <main className="flex-1 overflow-hidden">
        <div className={view === 'collection' ? 'h-full' : 'hidden'}>
          <CollectionBrowser
            onRefreshPrices={(id) => handleFindPrices(id)}
            crawling={crawling}
            crawlingReleaseId={crawlingReleaseId}
            crawlEvents={crawlEvents}
            crawlers={crawlers}
          />
        </div>
        <div className={view === 'settings' ? 'h-full overflow-y-auto' : 'hidden'}><Settings crawlers={crawlers} onCrawlersChange={setCrawlers} /></div>
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

      {/* Crawl status bar */}
      {drawerOpen && (
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
