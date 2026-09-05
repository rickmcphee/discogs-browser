import { useState, useEffect, useRef, memo } from 'react'
import { getSettings, saveSettings, setCrawlerEnabled } from '../api/client'
import type { Settings as SettingsType, Crawler } from '../api/types'
import { navButtonClass, secondaryButtonClass } from '../styles/buttons'
import { textInputClass, selectClass } from '../styles/inputs'
import { stackedTableClass, stackedBodyClass, stackedRowClass, stackedCellClass } from '../styles/tables'

interface SettingRow {
  key: keyof SettingsType
  label: string
  description: string
  type: 'password' | 'text' | 'number' | 'checkbox'
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
  {
    key: 'crawl_library_only',
    label: 'Library only',
    description: 'Only crawl marketplaces for records in someone\'s collection, wantlist or saved items. Off = crawl every record the enabled stores list. Turning it on discards queued crawls for other records; turning it off picks them up again on the next store refresh.',
    type: 'checkbox',
  },
]

interface Props {
  crawlers: Crawler[]
  onCrawlersChange: (crawlers: Crawler[]) => void
  onRefreshPrices: (mode: 'missing' | 'all') => void
  onRefreshStock: () => void
  isAdmin: boolean
  stockSyncBusy: boolean
  stockSyncCrawlerId: number | null
  onRefreshStoreCrawler: (crawlerId: number) => void
  priceRefreshBusy: boolean
}

// apiFetch throws Error(await r.text()), so err.message is FastAPI's raw JSON
// body for a handled error and a plain string for anything else. Every branch
// narrows to a string before returning: `detail` is only a string for the
// app's own HTTPExceptions -- FastAPI's own 422s make it an array of objects,
// which React throws on rendering -- and a rejection carrying no message at
// all must reach the fallback rather than throwing here.
function errorMessage(err: unknown, fallback: string): string {
  const raw = typeof (err as { message?: unknown })?.message === 'string'
    ? (err as { message: string }).message
    : typeof err === 'string' ? err : ''
  try {
    const parsed = JSON.parse(raw)
    if (typeof parsed?.detail === 'string' && parsed.detail) return parsed.detail
  } catch {
    // not JSON, use raw message
  }
  return raw || fallback
}

// Same ring the sync banner spins, sized per call site: a button that has
// swapped its glyph for a spinner has to keep the glyph's box, or the row
// twitches on every state change.
function Spinner({ className }: { className: string }) {
  return (
    <span
      aria-hidden="true"
      className={`block border-2 ${className} border-t-transparent rounded-full animate-spin`}
    />
  )
}

function toggleButtonClass(on: boolean): string {
  return `px-3 py-1 rounded-full text-xs font-medium transition-colors ${
    on ? 'bg-green-700 hover:bg-green-600 text-white' : 'bg-gray-700 hover:bg-gray-600 text-gray-400'
  }`
}

function Settings({
  crawlers, onCrawlersChange, onRefreshPrices, onRefreshStock, isAdmin,
  stockSyncBusy, stockSyncCrawlerId, onRefreshStoreCrawler, priceRefreshBusy,
}: Props) {
  // A stock sync with no crawler named is the bulk one, so the bulk button
  // owns the spinner then and the rows stay merely disabled. Inverted for a
  // single-store refresh: the row that was clicked spins, the bulk button is
  // only disabled.
  const bulkStockRefreshing = stockSyncBusy && stockSyncCrawlerId === null
  const [settings, setSettings] = useState<SettingsType>({
    crawl_delay_seconds: 30,
    consecutive_failure_limit: 10,
    crawl_schedule: '',
    crawl_schedule_mode: 'missing',
    ebay_app_id: '',
    ebay_cert_id: '',
    stock_schedule: '',
    crawl_library_only: false,
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
      <tr key={row.key} className={`border-b border-gray-800/50 ${stackedRowClass}`}>
        <td className={`pt-3 pb-1 text-left text-gray-300 font-medium align-top whitespace-nowrap md:py-3 md:pr-4 ${stackedCellClass}${first ? ' md:w-40' : ''}`}>
          {row.label}
        </td>
        <td className={`pb-2 text-left align-top md:py-3 md:pr-4 ${stackedCellClass}${first ? ' md:w-64' : ''}`}>
          {row.type === 'checkbox' ? (
            <input
              type="checkbox"
              aria-label={row.label}
              checked={Boolean(settings[row.key])}
              onChange={(e) =>
                setSettings({ ...settings, [row.key]: e.target.checked })
              }
              className="accent-white h-4 w-4 align-middle"
            />
          ) : row.type === 'number' ? (
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
        <td className={`pb-3 text-left text-gray-500 text-xs align-top leading-relaxed md:py-3 ${stackedCellClass}`}>
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
      <table className="w-full text-sm border-collapse mt-4 block md:table">
        {/* The stacked mobile row labels its own "Last run" inline; the toggle
            and the refresh icon say what they are. */}
        <thead className="hidden md:table-header-group">
          <tr className="text-xs text-gray-500 uppercase tracking-wider border-b border-gray-800">
            <th className="text-left py-2 pr-4 w-40">Site</th>
            {isAdmin && <th className="text-left py-2 pr-4 w-48">Last run</th>}
            {isAdmin && <th className="text-left py-2 pr-4">Crawl</th>}
            {isAdmin && showRefresh && <th className="text-left py-2 w-24">Refresh</th>}
          </tr>
        </thead>
        <tbody className={stackedBodyClass}>
          {crawlerList.map((c) => {
            // The row actually being scanned must not be dimmed by the same
            // disabled styling as the rows merely waiting on it. It inverts to
            // the lit nav pill instead, so the one store that is running is the
            // one thing in the table that stands out.
            const refreshing = showRefresh && stockSyncCrawlerId === c.id
            return (
              // Below the breakpoint the row is a flex line -- site name and its
              // two controls together, the last-run timestamp wrapped onto its
              // own line under them by `order-last basis-full`.
              <tr key={c.id} className={`flex flex-wrap items-center gap-x-3 border-b border-gray-800/50 py-2 md:table-row md:py-0${refreshing ? ' bg-gray-800/60' : ''}`}>
                <td className="min-w-0 flex-1 text-left text-gray-200 font-medium md:table-cell md:py-3 md:pr-4">
                  {c.base_url
                    ? <a href={c.base_url} target="_blank" rel="noreferrer"
                         title={c.genre_summary ?? undefined}
                         className="text-gray-400 hover:text-white underline">{c.site_name}</a>
                    : <span title={c.genre_summary ?? undefined}>{c.site_name}</span>}
                </td>
                {isAdmin && (
                  <td className="order-last basis-full text-left text-gray-500 text-xs md:table-cell md:order-none md:basis-auto md:py-3 md:pr-4">
                    <span className="md:hidden">Last run: </span>
                    {c.last_run ? new Date(c.last_run).toLocaleString() : '—'}
                  </td>
                )}
                {isAdmin && (
                  <td className="text-left md:table-cell md:py-3 md:pr-4">
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
                  <td className="text-left md:table-cell md:py-3">
                    <button
                      onClick={() => onRefreshStoreCrawler(c.id)}
                      disabled={!c.enabled || stockSyncBusy}
                      title={refreshing
                        ? `Refreshing ${c.site_name} catalog…`
                        : `Refresh ${c.site_name} catalog now`}
                      className={`flex h-11 w-11 items-center justify-center md:h-auto md:w-auto md:p-1.5 ${refreshing
                        ? navButtonClass(true)
                        : `disabled:opacity-30 disabled:cursor-not-allowed ${navButtonClass(false)}`}`}
                    >
                      {refreshing
                        ? <Spinner className="w-4 h-4 border-gray-950" />
                        : <span className="block text-base leading-none">↻</span>}
                    </button>
                  </td>
                )}
              </tr>
            )
          })}
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
      } catch (err) {
        if (seq !== latestSaveSeq.current) return
        setSettingsSaveError(errorMessage(err, 'Save failed'))
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
    setSettingsSaveError('')
    try {
      const { discarded } = await setCrawlerEnabled(crawler.id, !crawler.enabled)
      onCrawlersChange(crawlers.map((c) => c.id === crawler.id ? { ...c, enabled: !c.enabled } : c))
      setDiscardedNotice(discarded ? { crawlerId: crawler.id, count: discarded } : null)
    } catch (err) {
      // The row keeps showing its old state because onCrawlersChange never
      // ran -- the button reflects the server, not the click.
      setSettingsSaveError(errorMessage(err, 'Could not change this crawler'))
    }
  }

  return (
    <div className="max-w-3xl mx-auto p-4 space-y-8 md:p-6 md:space-y-10">

      {/* Crawler Management */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-1 text-left">
          {isAdmin ? 'Marketplace Management' : 'Marketplaces'}
        </h2>
        <p className="text-sm text-gray-500 mb-4 text-left">
          {isAdmin
            ? <>Run price crawlers on a schedule. Leave blank to disable. Example: <code className="text-gray-400 font-mono">0 2 * * *</code> = 2 am daily.</>
            : 'Choose which marketplaces\' prices you want to see — for items in your collection or wantlist, and items found in Stores.'}
        </p>
        {isAdmin && settingsSaveError && <p className="text-xs text-red-400 mb-3 text-left">{settingsSaveError}</p>}
        {isAdmin && (
          <>
            <table className={`mb-4 ${stackedTableClass}`}>
              <tbody className={stackedBodyClass}>
                {CRAWLER_SETTING_ROWS.map((row, i) => renderSettingRow(row, i === 0))}
              </tbody>
            </table>
            <table className={stackedTableClass}>
              <tbody className={stackedBodyClass}>
                <tr className={`border-b border-gray-800/50 ${stackedRowClass}`}>
                  <td className={`pt-3 pb-1 text-left text-gray-300 font-medium align-top whitespace-nowrap md:py-3 md:pr-4 md:w-40 ${stackedCellClass}`}>Schedule</td>
                  <td className={`pb-2 text-left align-top md:py-3 md:pr-4 md:w-64 ${stackedCellClass}`}>
                    <input
                      type="text"
                      value={settings.crawl_schedule ?? ''}
                      placeholder="0 2 * * *"
                      onChange={(e) => setSettings({ ...settings, crawl_schedule: e.target.value })}
                      className={`w-full px-3 py-1 font-mono text-xs ${textInputClass()}`}
                    />
                  </td>
                  <td className={`pb-3 text-left text-gray-500 text-xs align-top leading-relaxed md:py-3 ${stackedCellClass}`}>
                    Cron expression (5 fields: min hour day month weekday). Empty = disabled.
                  </td>
                </tr>
                <tr className={`border-b border-gray-800/50 ${stackedRowClass}`}>
                  <td className={`pt-3 pb-1 text-left text-gray-300 font-medium align-top whitespace-nowrap md:py-3 md:pr-4 ${stackedCellClass}`}>Mode</td>
                  <td className={`pb-2 text-left align-top md:py-3 md:pr-4 ${stackedCellClass}`}>
                    <select
                      value={settings.crawl_schedule_mode ?? 'missing'}
                      onChange={(e) => setSettings({ ...settings, crawl_schedule_mode: e.target.value as 'missing' | 'all' })}
                      className={`w-full px-3 py-1 ${selectClass()}`}
                    >
                      <option value="missing">Missing only</option>
                      <option value="all">All records</option>
                    </select>
                  </td>
                  <td className={`pb-3 text-left text-gray-500 text-xs align-top leading-relaxed md:py-3 ${stackedCellClass}`}>
                    What to crawl on each scheduled run.
                  </td>
                </tr>
                <tr className={`border-b border-gray-800/50 ${stackedRowClass}`}>
                  <td className={`hidden md:table-cell md:py-3 md:pr-4 md:align-top md:w-40`}></td>
                  <td className={`pb-2 text-left align-top md:py-3 md:pr-4 ${stackedCellClass}`}>
                    <button
                      onClick={() => onRefreshPrices(settings.crawl_schedule_mode as 'missing' | 'all' ?? 'missing')}
                      disabled={priceRefreshBusy}
                      className={`px-3 py-1 text-xs inline-flex items-center gap-2 ${secondaryButtonClass()}`}
                    >
                      {priceRefreshBusy && <Spinner className="w-3 h-3 border-white" />}
                      {priceRefreshBusy ? 'Starting…' : 'Refresh'}
                    </button>
                  </td>
                  <td className={`pb-3 text-left text-gray-500 text-xs align-top leading-relaxed md:py-3 ${stackedCellClass}`}>
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
          {isAdmin ? 'Store Management' : 'Stores'}
        </h2>
        <p className="text-sm text-gray-500 mb-4 text-left">
          {isAdmin
            ? 'Scan an entire site\'s in-stock catalog, independent of your collection. Results appear in the Store tab. Leave schedule blank to disable.'
            : 'Choose which stores\' items you want to see in the Store tab.'}
        </p>
        {isAdmin && (
          <table className={stackedTableClass}>
            <tbody className={stackedBodyClass}>
              <tr className={`border-b border-gray-800/50 ${stackedRowClass}`}>
                <td className={`pt-3 pb-1 text-left text-gray-300 font-medium align-top whitespace-nowrap md:py-3 md:pr-4 md:w-40 ${stackedCellClass}`}>Schedule</td>
                <td className={`pb-2 text-left align-top md:py-3 md:pr-4 md:w-64 ${stackedCellClass}`}>
                  <input
                    type="text"
                    value={settings.stock_schedule ?? ''}
                    placeholder="0 3 * * *"
                    onChange={(e) => setSettings({ ...settings, stock_schedule: e.target.value })}
                    className={`w-full px-3 py-1 font-mono text-xs ${textInputClass()}`}
                  />
                </td>
                <td className={`pb-3 text-left text-gray-500 text-xs align-top leading-relaxed md:py-3 ${stackedCellClass}`}>
                  Cron expression (5 fields: min hour day month weekday). Empty = disabled.
                </td>
              </tr>
              <tr className={`border-b border-gray-800/50 ${stackedRowClass}`}>
                <td className={`hidden md:table-cell md:py-3 md:pr-4 md:align-top md:w-40`}></td>
                <td className={`pb-2 text-left align-top md:py-3 md:pr-4 ${stackedCellClass}`}>
                  <button
                    onClick={onRefreshStock}
                    disabled={stockSyncBusy}
                    className={`px-3 py-1 text-xs inline-flex items-center gap-2 ${
                      bulkStockRefreshing ? '' : 'disabled:opacity-50 '
                    }${secondaryButtonClass()}`}
                  >
                    {bulkStockRefreshing && <Spinner className="w-3 h-3 border-white" />}
                    {bulkStockRefreshing ? 'Refreshing…' : 'Refresh'}
                  </button>
                </td>
                <td className={`pb-3 text-left text-gray-500 text-xs align-top leading-relaxed md:py-3 ${stackedCellClass}`}>
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
