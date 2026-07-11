import { useState, useEffect } from 'react'
import { getSettings, saveSettings, setCrawlerEnabled, changePassword, logout } from '../api/client'
import type { Settings as SettingsType, Crawler } from '../api/types'

interface SettingRow {
  key: keyof SettingsType
  label: string
  description: string
  type: 'password' | 'text' | 'number' | 'boolean'
  placeholder?: string
}

const SETTING_ROWS: SettingRow[] = [
  {
    key: 'discogs_token',
    label: 'Discogs token',
    description: 'Personal access token from discogs.com/settings/developers',
    type: 'password',
    placeholder: 'your token',
  },
  {
    key: 'ebay_app_id',
    label: 'eBay App ID',
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
    key: 'plex_base_url',
    label: 'Plex server address',
    description: 'Host and port of your Plex Media Server on the LAN, e.g. 192.168.1.50:32400.',
    type: 'text',
    placeholder: '192.168.1.50:32400',
  },
  {
    key: 'plex_token',
    label: 'Plex token',
    description: 'X-Plex-Token for your server. Find it via a browser request while logged into Plex Web (see Plex support docs).',
    type: 'password',
    placeholder: 'your Plex token',
  },
  {
    key: 'anthropic_api_key',
    label: 'Anthropic API key',
    description: 'Used to judge Store items against your collection for the Recommended filter. Get one at platform.claude.com.',
    type: 'password',
    placeholder: 'sk-ant-...',
  },
  {
    key: 'recommendation_item_limit',
    label: 'Recommendation item limit',
    description: 'Maximum number of unprocessed Store items evaluated by Claude for recommendation each time. Extra items are evaluated on a later run. 0 = no limit.',
    type: 'number',
  },
  {
    key: 'debug_screenshot_interval',
    label: 'Screenshot interval',
    description: '0 = off · 1 = every search · N = every Nth. First search always captured when > 0.',
    type: 'number',
  },
  {
    key: 'crawl_delay_seconds',
    label: 'Crawl delay (s)',
    description: 'Max seconds to wait between requests during bulk crawl. Actual wait is 50–100% of this value. Single-item refreshes always use a short delay.',
    type: 'number',
  },
  {
    key: 'consecutive_failure_limit',
    label: 'Failure limit',
    description: 'Stop bulk crawl after this many consecutive failures (not_found or error). Only active when shuffle is on. 0 = disabled.',
    type: 'number',
  },
  {
    key: 'shuffle_crawl_order',
    label: 'Shuffle crawl order',
    description: 'Randomize the order records are crawled. Reduces bot detection patterns.',
    type: 'boolean',
  },
]

interface Props {
  crawlers: Crawler[]
  onCrawlersChange: (crawlers: Crawler[]) => void
  onRefreshCollection: (mode: 'all' | 'new') => void
  onRefreshPrices: (mode: 'missing' | 'all') => void
  onRefreshStock: () => void
  onRefreshRecommendations: () => void
  onExportRecommendations: () => void
  onClearRecommendations: () => void
  hasJudgedItems: boolean
}

export default function Settings({ crawlers, onCrawlersChange, onRefreshCollection, onRefreshPrices, onRefreshStock, onRefreshRecommendations, onExportRecommendations, onClearRecommendations, hasJudgedItems }: Props) {
  const [settings, setSettings] = useState<SettingsType>({
    discogs_token: '',
    debug_screenshot_interval: 20,
    shuffle_crawl_order: true,
    crawl_delay_seconds: 30,
    consecutive_failure_limit: 10,
    crawl_schedule: '',
    crawl_schedule_mode: 'missing',
    collection_schedule: '',
    collection_schedule_mode: 'all',
    ebay_app_id: '',
    ebay_cert_id: '',
    plex_base_url: '',
    plex_token: '',
    stock_schedule: '',
    anthropic_api_key: '',
    recommendation_item_limit: 300,
  })
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [authCode, setAuthCode] = useState('')
  const [passwordMessage, setPasswordMessage] = useState('')

  const releaseCrawlers = crawlers.filter((c) => c.crawler_type !== 'catalog')
  const catalogCrawlers = crawlers.filter((c) => c.crawler_type === 'catalog')

  useEffect(() => {
    getSettings().then(setSettings)
  }, [])

  async function handleSave() {
    setSaving(true)
    try {
      await saveSettings(settings)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } finally {
      setSaving(false)
    }
  }

  async function handleToggleCrawler(crawler: Crawler) {
    await setCrawlerEnabled(crawler.id, !crawler.enabled)
    onCrawlersChange(crawlers.map((c) => c.id === crawler.id ? { ...c, enabled: !c.enabled } : c))
  }

  async function submitPasswordChange() {
    try {
      await changePassword(currentPassword, newPassword, authCode)
      setPasswordMessage('Password changed.')
      setCurrentPassword('')
      setNewPassword('')
      setAuthCode('')
    } catch {
      setPasswordMessage('Failed — check current password and code.')
    }
  }

  return (
    <div className="max-w-3xl mx-auto p-6 space-y-10">

      {/* Settings table */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-white">Settings</h2>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 rounded text-sm font-medium transition-colors"
          >
            {saved ? '✓ Saved' : saving ? 'Saving…' : 'Save'}
          </button>
        </div>
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="text-xs text-gray-500 uppercase tracking-wider border-b border-gray-800">
              <th className="text-left py-2 pr-4 w-40 align-top">Setting</th>
              <th className="text-left py-2 pr-4 w-64 align-top">Value</th>
              <th className="text-left py-2 align-top">Description</th>
            </tr>
          </thead>
          <tbody>
            {SETTING_ROWS.map((row) => (
              <tr key={row.key} className="border-b border-gray-800/50">
                <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap">
                  {row.label}
                </td>
                <td className="py-3 pr-4 text-left align-top">
                  {row.type === 'boolean' ? (
                    <button
                      onClick={() => setSettings({ ...settings, [row.key]: !settings[row.key] })}
                      className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                        settings[row.key]
                          ? 'bg-green-700 hover:bg-green-600 text-white'
                          : 'bg-gray-700 hover:bg-gray-600 text-gray-400'
                      }`}
                    >
                      {settings[row.key] ? 'On' : 'Off'}
                    </button>
                  ) : row.type === 'number' ? (
                    <input
                      type="number"
                      min={0}
                      value={settings[row.key] as number}
                      onChange={(e) =>
                        setSettings({ ...settings, [row.key]: parseInt(e.target.value) || 0 })
                      }
                      className="w-24 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white focus:outline-none focus:border-indigo-500"
                    />
                  ) : (
                    <input
                      type={row.type === 'text' ? 'text' : 'password'}
                      value={settings[row.key] as string}
                      placeholder={row.placeholder}
                      onChange={(e) =>
                        setSettings({ ...settings, [row.key]: e.target.value })
                      }
                      className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                    />
                  )}
                </td>
                <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                  {row.description}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* Collection Management */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-1 text-left">Collection Management</h2>
        <p className="text-sm text-gray-500 mb-4 text-left">
          Sync your Discogs collection on a schedule. Leave blank to disable.
          Example: <code className="text-gray-400 font-mono">0 1 * * *</code> = 1 am daily.
        </p>
        <table className="w-full text-sm border-collapse">
          <tbody>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap w-40">Schedule</td>
              <td className="py-3 pr-4 text-left align-top w-64">
                <input
                  type="text"
                  value={settings.collection_schedule ?? ''}
                  placeholder="0 1 * * *"
                  onChange={(e) => setSettings({ ...settings, collection_schedule: e.target.value })}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500 font-mono text-xs"
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
                  value={settings.collection_schedule_mode ?? 'all'}
                  onChange={(e) => setSettings({ ...settings, collection_schedule_mode: e.target.value as 'all' | 'new' })}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white focus:outline-none focus:border-indigo-500"
                >
                  <option value="all">All records</option>
                  <option value="new">New records only</option>
                </select>
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                What to sync on each scheduled run.
              </td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left align-top whitespace-nowrap w-40"></td>
              <td className="py-3 pr-4 text-left align-top">
                <button
                  onClick={() => onRefreshCollection(settings.collection_schedule_mode as 'all' | 'new' ?? 'all')}
                  className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 active:bg-indigo-800 rounded text-xs font-medium transition-colors"
                >
                  Refresh Now
                </button>
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Sync collection and wishlist from Discogs immediately. Fetches barcodes for new records.
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      {/* Crawler Management */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-1 text-left">Crawler Management</h2>
        <p className="text-sm text-gray-500 mb-4 text-left">
          Run price crawlers on a schedule. Leave blank to disable.
          Example: <code className="text-gray-400 font-mono">0 2 * * *</code> = 2 am daily.
        </p>
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
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500 font-mono text-xs"
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
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white focus:outline-none focus:border-indigo-500"
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
                  className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 active:bg-indigo-800 rounded text-xs font-medium transition-colors"
                >
                  Refresh Now
                </button>
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Run price crawlers immediately.
              </td>
            </tr>
          </tbody>
        </table>
        {releaseCrawlers.length === 0 ? (
          <p className="text-gray-500 text-sm text-left mt-4">No crawlers configured.</p>
        ) : (
          <table className="w-full text-sm border-collapse mt-4">
            <thead>
              <tr className="text-xs text-gray-500 uppercase tracking-wider border-b border-gray-800">
                <th className="text-left py-2 pr-4 w-40">Site</th>
                <th className="text-left py-2 pr-4 w-48">Last run</th>
                <th className="text-left py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {releaseCrawlers.map((c) => (
                <tr key={c.id} className="border-b border-gray-800/50">
                  <td className="py-3 pr-4 text-left text-gray-200 font-medium">
                    {c.base_url
                      ? <a href={c.base_url} target="_blank" rel="noreferrer"
                           className="text-indigo-400 hover:text-indigo-300 underline">{c.site_name}</a>
                      : c.site_name}
                  </td>
                  <td className="py-3 pr-4 text-left text-gray-500 text-xs">
                    {c.last_run ? new Date(c.last_run).toLocaleString() : '—'}
                  </td>
                  <td className="py-3 text-left">
                    <button
                      onClick={() => handleToggleCrawler(c)}
                      className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                        c.enabled
                          ? 'bg-green-700 hover:bg-green-600 text-white'
                          : 'bg-gray-700 hover:bg-gray-600 text-gray-400'
                      }`}
                    >
                      {c.enabled ? 'Enabled' : 'Disabled'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* Store Management */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-1 text-left">Store Management</h2>
        <p className="text-sm text-gray-500 mb-4 text-left">
          Scan an entire site's in-stock catalog, independent of your collection. Results appear in the Store tab.
          Leave schedule blank to disable.
        </p>
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
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500 font-mono text-xs"
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
                  className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 active:bg-indigo-800 rounded text-xs font-medium transition-colors"
                >
                  Refresh Now
                </button>
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Scan all enabled catalog crawlers immediately.
              </td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left align-top whitespace-nowrap w-40"></td>
              <td className="py-3 pr-4 text-left align-top">
                <button
                  onClick={onRefreshRecommendations}
                  disabled={!settings.anthropic_api_key}
                  className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 active:bg-indigo-800 disabled:opacity-50 rounded text-xs font-medium transition-colors"
                >
                  Refresh Recommendations
                </button>
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Evaluate unprocessed Store items for recommendation, without a full catalog re-crawl. Requires an Anthropic API key above.
              </td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left align-top whitespace-nowrap w-40"></td>
              <td className="py-3 pr-4 text-left align-top">
                <button
                  onClick={onExportRecommendations}
                  disabled={!hasJudgedItems}
                  className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 active:bg-indigo-800 disabled:opacity-50 rounded text-xs font-medium transition-colors"
                >
                  Export Recommendations
                </button>
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Download recommended Store items as CSV (artist, title, format, price, source, link, reason).
              </td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left align-top whitespace-nowrap w-40"></td>
              <td className="py-3 pr-4 text-left align-top">
                <button
                  onClick={onClearRecommendations}
                  disabled={!hasJudgedItems}
                  className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 active:bg-indigo-800 disabled:opacity-50 rounded text-xs font-medium transition-colors"
                >
                  Clear Recommendations
                </button>
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Remove all recommendation judgments, recommended and not-recommended, so every Store item is re-evaluated from scratch on the next run.
              </td>
            </tr>
          </tbody>
        </table>
        {catalogCrawlers.length === 0 ? (
          <p className="text-gray-500 text-sm text-left mt-4">No catalog crawlers configured.</p>
        ) : (
          <table className="w-full text-sm border-collapse mt-4">
            <thead>
              <tr className="text-xs text-gray-500 uppercase tracking-wider border-b border-gray-800">
                <th className="text-left py-2 pr-4 w-40">Site</th>
                <th className="text-left py-2 pr-4 w-48">Last run</th>
                <th className="text-left py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {catalogCrawlers.map((c) => (
                <tr key={c.id} className="border-b border-gray-800/50">
                  <td className="py-3 pr-4 text-left text-gray-200 font-medium">
                    {c.base_url
                      ? <a href={c.base_url} target="_blank" rel="noreferrer"
                           className="text-indigo-400 hover:text-indigo-300 underline">{c.site_name}</a>
                      : c.site_name}
                  </td>
                  <td className="py-3 pr-4 text-left text-gray-500 text-xs">
                    {c.last_run ? new Date(c.last_run).toLocaleString() : '—'}
                  </td>
                  <td className="py-3 text-left">
                    <button
                      onClick={() => handleToggleCrawler(c)}
                      className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                        c.enabled
                          ? 'bg-green-700 hover:bg-green-600 text-white'
                          : 'bg-gray-700 hover:bg-gray-600 text-gray-400'
                      }`}
                    >
                      {c.enabled ? 'Enabled' : 'Disabled'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* Account & Security */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-1 text-left">Account & Security</h2>
        <p className="text-sm text-gray-500 mb-4 text-left">
          Change your password or log out of this session.
        </p>
        <table className="w-full text-sm border-collapse">
          <tbody>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap w-40">Current password</td>
              <td className="py-3 pr-4 text-left align-top w-64">
                <input
                  type="password"
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                />
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed"></td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap">New password</td>
              <td className="py-3 pr-4 text-left align-top">
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                />
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed"></td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap">Authenticator code</td>
              <td className="py-3 pr-4 text-left align-top">
                <input
                  type="text"
                  inputMode="numeric"
                  value={authCode}
                  onChange={(e) => setAuthCode(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                />
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Current code from your authenticator app.
              </td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left align-top whitespace-nowrap w-40"></td>
              <td className="py-3 pr-4 text-left align-top">
                <div className="flex items-center gap-3">
                  <button
                    onClick={submitPasswordChange}
                    className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 rounded text-xs font-medium transition-colors"
                  >
                    Change password
                  </button>
                  <button
                    onClick={() => logout().then(() => window.location.reload())}
                    className="px-3 py-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded text-xs font-medium transition-colors"
                  >
                    Log out
                  </button>
                </div>
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                {passwordMessage}
              </td>
            </tr>
          </tbody>
        </table>
      </section>

    </div>
  )
}
