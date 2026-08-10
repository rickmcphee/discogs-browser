import { useState, useEffect, useRef, memo } from 'react'
import { getSettings, saveSettings, setCrawlerEnabled } from '../api/client'
import type { Settings as SettingsType, Crawler } from '../api/types'
import { navButtonClass, secondaryButtonClass } from '../styles/buttons'
import { textInputClass, selectClass } from '../styles/inputs'

interface SettingRow {
  key: keyof SettingsType
  label: string
  description: string
  type: 'password' | 'text' | 'number'
  placeholder?: string
}

const CRAWLER_SETTING_ROWS: SettingRow[] = [
  {
    key: 'ebay_app_id',
    label: 'eBay Client ID',
    description: 'eBay Client ID (App ID) for Browse API access.',
    type: 'password',
    placeholder: 'your App ID',
  },
  {
    key: 'ebay_cert_id',
    label: 'eBay Cert ID',
    description: 'eBay Client Secret (Cert ID) for Browse API access.',
    type: 'password',
    placeholder: 'your Cert ID',
  },
  {
    key: 'crawl_delay_seconds',
    label: 'Crawl delay',
    description: 'Max seconds to wait between requests during bulk crawl. Actual wait is 50–100% of this value. Single-item refreshes always use a short delay.',
    type: 'number',
  },
  {
    key: 'consecutive_failure_limit',
    label: 'Failure limit',
    description: 'Pause crawling a site for 30 minutes after this many consecutive failures (not_found or error) in a row. 0 = disabled.',
    type: 'number',
  },
]

interface Props {
  crawlers: Crawler[]
  onCrawlersChange: (crawlers: Crawler[]) => void
  onRefreshPrices: (mode: 'missing' | 'all') => void
  onRefreshStock: () => void
  isAdmin: boolean
  hiddenCrawlerIds: number[]
  onToggleCrawlerView: (crawlerId: number) => void
  stockSyncBusy: boolean
  stockSyncCrawlerId: number | null
  onRefreshStoreCrawler: (crawlerId: number) => void
}

function toggleButtonClass(on: boolean): string {
  return `px-3 py-1 rounded-full text-xs font-medium transition-colors ${
    on ? 'bg-green-700 hover:bg-green-600 text-white' : 'bg-gray-700 hover:bg-gray-600 text-gray-400'
  }`
}

function Settings({
  crawlers, onCrawlersChange, onRefreshPrices, onRefreshStock, isAdmin, hiddenCrawlerIds, onToggleCrawlerView,
  stockSyncBusy, stockSyncCrawlerId, onRefreshStoreCrawler,
}: Props) {
  const [settings, setSettings] = useState<SettingsType>({
    crawl_delay_seconds: 30,
    consecutive_failure_limit: 10,
    crawl_schedule: '',
    crawl_schedule_mode: 'missing',
    ebay_app_id: '',
    ebay_cert_id: '',
    stock_schedule: '',
  })
  const [settingsSaveError, setSettingsSaveError] = useState('')
  const skipNextAutoSave = useRef(true)
  const saveChainRef = useRef<Promise<void>>(Promise.resolve())
  const latestSaveSeq = useRef(0)
  // Value never read — its only job is forcing the debounce effect below to
  // re-run once settings load, even when the fetched values equal the
  // useState defaults above and React would otherwise bail out of re-rendering.
  const [settingsLoaded, setSettingsLoaded] = useState(false)
  const [discardedNotice, setDiscardedNotice] = useState<{ crawlerId: number; count: number } | null>(null)

  const releaseCrawlers = crawlers.filter((c) => c.crawler_type === 'release')
  const catalogCrawlers = crawlers.filter((c) => c.crawler_type === 'catalog' || c.crawler_type === 'catalog_browser')
  const shownReleaseCrawlers = isAdmin ? releaseCrawlers : releaseCrawlers.filter((c) => c.enabled)
  const shownCatalogCrawlers = isAdmin ? catalogCrawlers : catalogCrawlers.filter((c) => c.enabled)

  function renderSettingRow(row: SettingRow, first: boolean) {
    return (
      <tr key={row.key} className="border-b border-gray-800/50">
        <td className={`py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap${first ? ' w-40' : ''}`}>
          {row.label}
        </td>
        <td className={`py-3 pr-4 text-left align-top${first ? ' w-64' : ''}`}>
          {row.type === 'number' ? (
            <input
              type="number"
              min={0}
              aria-label={row.label}
              value={settings[row.key] as number}
              onChange={(e) =>
                setSettings({ ...settings, [row.key]: parseInt(e.target.value) || 0 })
              }
              className={`w-24 px-3 py-1 ${textInputClass()}`}
            />
          ) : (
            <input
              type={row.type === 'text' ? 'text' : 'password'}
              aria-label={row.label}
              value={settings[row.key] as string}
              placeholder={row.placeholder}
              onChange={(e) =>
                setSettings({ ...settings, [row.key]: e.target.value })
              }
              className={`w-full px-3 py-1 ${textInputClass()}`}
            />
          )}
        </td>
        <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
          {row.description}
        </td>
      </tr>
    )
  }

  function renderCrawlerTable(crawlerList: Crawler[], emptyMessage: string, showRefresh = false) {
    if (crawlerList.length === 0) {
      return <p className="text-gray-500 text-sm text-left mt-4">{emptyMessage}</p>
    }
    return (
      <table className="w-full text-sm border-collapse mt-4">
        <thead>
          <tr className="text-xs text-gray-500 uppercase tracking-wider border-b border-gray-800">
            <th className="text-left py-2 pr-4 w-40">Site</th>
            {isAdmin && <th className="text-left py-2 pr-4 w-48">Last run</th>}
            <th className="text-left py-2 pr-4">View</th>
            {isAdmin && <th className="text-left py-2 pr-4">Crawl</th>}
            {isAdmin && showRefresh && <th className="text-left py-2 w-24">Refresh</th>}
          </tr>
        </thead>
        <tbody>
          {crawlerList.map((c) => (
            <tr key={c.id} className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-200 font-medium">
                {c.base_url
                  ? <a href={c.base_url} target="_blank" rel="noreferrer"
                       className="text-gray-400 hover:text-white underline">{c.site_name}</a>
                  : c.site_name}
              </td>
              {isAdmin && (
                <td className="py-3 pr-4 text-left text-gray-500 text-xs">
                  {c.last_run ? new Date(c.last_run).toLocaleString() : '—'}
                </td>
              )}
              <td className="py-3 pr-4 text-left">
                <button
                  onClick={() => onToggleCrawlerView(c.id)}
                  className={toggleButtonClass(!hiddenCrawlerIds.includes(c.id))}
                >
                  {hiddenCrawlerIds.includes(c.id) ? 'Hidden' : 'Visible'}
                </button>
              </td>
              {isAdmin && (
                <td className="py-3 pr-4 text-left">
                  <button
                    onClick={() => handleToggleCrawler(c)}
                    className={toggleButtonClass(c.enabled)}
                  >
                    {c.enabled ? 'Enabled' : 'Disabled'}
                  </button>
                  {discardedNotice?.crawlerId === c.id && (
                    <span className="ml-2 text-xs text-gray-500">
                      {discardedNotice.count} queued {discardedNotice.count === 1 ? 'job' : 'jobs'} discarded
                    </span>
                  )}
                </td>
              )}
              {isAdmin && showRefresh && (
                <td className="py-3 text-left">
                  <button
                    onClick={() => onRefreshStoreCrawler(c.id)}
                    disabled={!c.enabled || stockSyncBusy}
                    title={`Refresh ${c.site_name} catalog now`}
                    className={`p-1.5 disabled:opacity-30 disabled:cursor-not-allowed ${navButtonClass(false)}`}
                  >
                    <span className="block text-base leading-none">
                      {stockSyncCrawlerId === c.id ? '⟳' : '↻'}
                    </span>
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    )
  }

  useEffect(() => {
    if (!isAdmin) return
    getSettings().then((s) => {
      setSettings(s)
      skipNextAutoSave.current = true
      setSettingsLoaded(true)
    }).catch(() => {})
  }, [isAdmin])

  function saveSettingsNow() {
    const seq = ++latestSaveSeq.current
    saveChainRef.current = saveChainRef.current.then(async () => {
      setSettingsSaveError('')
      try {
        await saveSettings(settings)
      } catch (err: any) {
        if (seq !== latestSaveSeq.current) return
        let message = err.message || 'Save failed'
        try {
          const parsed = JSON.parse(err.message)
          if (parsed.detail) message = parsed.detail
        } catch {
          // not JSON, use raw message
        }
        setSettingsSaveError(message)
      }
    })
  }

  useEffect(() => {
    if (skipNextAutoSave.current) {
      skipNextAutoSave.current = false
      return
    }
    setSettingsSaveError('')
    const timer = setTimeout(() => {
      saveSettingsNow()
    }, 800)
    return () => clearTimeout(timer)
  }, [settings, settingsLoaded])

  async function handleToggleCrawler(crawler: Crawler) {
    const { discarded } = await setCrawlerEnabled(crawler.id, !crawler.enabled)
    onCrawlersChange(crawlers.map((c) => c.id === crawler.id ? { ...c, enabled: !c.enabled } : c))
    setDiscardedNotice(discarded ? { crawlerId: crawler.id, count: discarded } : null)
  }

  return (
    <div className="max-w-3xl mx-auto p-6 space-y-10">

      {/* Crawler Management */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-1 text-left">
          {isAdmin ? 'Crawler Management' : 'Store Sources'}
        </h2>
        <p className="text-sm text-gray-500 mb-4 text-left">
          {isAdmin
            ? <>Run price crawlers on a schedule. Leave blank to disable. Example: <code className="text-gray-400 font-mono">0 2 * * *</code> = 2 am daily.</>
            : 'Choose which stores\' items you want to see in the Store tab.'}
        </p>
        {isAdmin && settingsSaveError && <p className="text-xs text-red-400 mb-3 text-left">{settingsSaveError}</p>}
        {isAdmin && (
          <>
            <table className="w-full text-sm border-collapse mb-4">
              <tbody>
                {CRAWLER_SETTING_ROWS.map((row, i) => renderSettingRow(row, i === 0))}
              </tbody>
            </table>
            <table className="w-full text-sm border-collapse">
              <tbody>
                <tr className="border-b border-gray-800/50">
                  <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap w-40">Schedule</td>
                  <td className="py-3 pr-4 text-left align-top w-64">
                    <input
                      type="text"
                      value={settings.crawl_schedule ?? ''}
                      placeholder="0 2 * * *"
                      onChange={(e) => setSettings({ ...settings, crawl_schedule: e.target.value })}
                      className={`w-full px-3 py-1 font-mono text-xs ${textInputClass()}`}
                    />
                  </td>
                  <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                    Cron expression (5 fields: min hour day month weekday). Empty = disabled.
                  </td>
                </tr>
                <tr className="border-b border-gray-800/50">
                  <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap">Mode</td>
                  <td className="py-3 pr-4 text-left align-top">
                    <select
                      value={settings.crawl_schedule_mode ?? 'missing'}
                      onChange={(e) => setSettings({ ...settings, crawl_schedule_mode: e.target.value as 'missing' | 'all' })}
                      className={`w-full px-3 py-1 ${selectClass()}`}
                    >
                      <option value="missing">Missing only</option>
                      <option value="all">All records</option>
                    </select>
                  </td>
                  <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                    What to crawl on each scheduled run.
                  </td>
                </tr>
                <tr className="border-b border-gray-800/50">
                  <td className="py-3 pr-4 text-left align-top whitespace-nowrap w-40"></td>
                  <td className="py-3 pr-4 text-left align-top">
                    <button
                      onClick={() => onRefreshPrices(settings.crawl_schedule_mode as 'missing' | 'all' ?? 'missing')}
                      className={`px-3 py-1 text-xs ${secondaryButtonClass()}`}
                    >
                      Refresh
                    </button>
                  </td>
                  <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                    Run price crawlers immediately.
                  </td>
                </tr>
              </tbody>
            </table>
          </>
        )}
        {renderCrawlerTable(shownReleaseCrawlers, 'No crawlers configured.')}
      </section>

      {/* Store Management */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-1 text-left">
          {isAdmin ? 'Store Management' : 'Store Catalog Sources'}
        </h2>
        <p className="text-sm text-gray-500 mb-4 text-left">
          {isAdmin
            ? 'Scan an entire site\'s in-stock catalog, independent of your collection. Results appear in the Store tab. Leave schedule blank to disable.'
            : 'Choose which stores\' items you want to see in the Store tab.'}
        </p>
        {isAdmin && (
          <table className="w-full text-sm border-collapse">
            <tbody>
              <tr className="border-b border-gray-800/50">
                <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap w-40">Schedule</td>
                <td className="py-3 pr-4 text-left align-top w-64">
                  <input
                    type="text"
                    value={settings.stock_schedule ?? ''}
                    placeholder="0 3 * * *"
                    onChange={(e) => setSettings({ ...settings, stock_schedule: e.target.value })}
                    className={`w-full px-3 py-1 font-mono text-xs ${textInputClass()}`}
                  />
                </td>
                <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                  Cron expression (5 fields: min hour day month weekday). Empty = disabled.
                </td>
              </tr>
              <tr className="border-b border-gray-800/50">
                <td className="py-3 pr-4 text-left align-top whitespace-nowrap w-40"></td>
                <td className="py-3 pr-4 text-left align-top">
                  <button
                    onClick={onRefreshStock}
                    disabled={stockSyncBusy}
                    className={`px-3 py-1 text-xs disabled:opacity-50 ${secondaryButtonClass()}`}
                  >
                    Refresh
                  </button>
                </td>
                <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                  Scan all enabled catalog crawlers immediately.
                </td>
              </tr>
            </tbody>
          </table>
        )}
        {renderCrawlerTable(shownCatalogCrawlers, 'No catalog crawlers configured.', true)}
      </section>

    </div>
  )
}

export default memo(Settings)
