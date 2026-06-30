import type {
  ReleasesResponse, Crawler, Settings, SortField, SortOrder, CrawlStatus, CollectionStatus, ScreenshotSession,
} from './types'

const BASE = '/api'

export async function checkHealth(): Promise<boolean> {
  try {
    const r = await fetch(`${BASE}/health`)
    // Any non-5xx means the backend is reachable (5xx = nginx gateway error)
    return r.status < 500
  } catch {
    return false
  }
}

export async function getCollectionStatus(): Promise<CollectionStatus> {
  const r = await fetch(`${BASE}/collection/status`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function refreshCollection(mode?: 'all' | 'new'): Promise<{ started: boolean; running: boolean }> {
  const url = mode === 'new' ? `${BASE}/collection/refresh?mode=new` : `${BASE}/collection/refresh`
  const r = await fetch(url, { method: 'POST' })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function getReleases(params: {
  search?: string
  artist?: string
  sort?: SortField
  order?: SortOrder
  page?: number
  per_page?: number
}): Promise<ReleasesResponse> {
  const q = new URLSearchParams()
  if (params.search) q.set('search', params.search)
  if (params.artist) q.set('artist', params.artist)
  if (params.sort) q.set('sort', params.sort)
  if (params.order) q.set('order', params.order)
  if (params.page) q.set('page', String(params.page))
  if (params.per_page) q.set('per_page', String(params.per_page))
  const r = await fetch(`${BASE}/releases?${q}`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function getArtists(): Promise<string[]> {
  const r = await fetch(`${BASE}/artists`)
  if (!r.ok) throw new Error(await r.text())
  const data = await r.json()
  return data.artists
}

export async function getCrawlers(): Promise<Crawler[]> {
  const r = await fetch(`${BASE}/crawlers`)
  if (!r.ok) throw new Error(await r.text())
  const data = await r.json()
  return data.crawlers
}

export async function getSettings(): Promise<Settings> {
  const r = await fetch(`${BASE}/settings`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function saveSettings(settings: Settings): Promise<void> {
  const r = await fetch(`${BASE}/settings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  })
  if (!r.ok) throw new Error(await r.text())
}

export async function setCrawlerEnabled(id: number, enabled: boolean): Promise<void> {
  const r = await fetch(`${BASE}/crawlers/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!r.ok) throw new Error(await r.text())
}

export async function getCrawlStatus(): Promise<CrawlStatus> {
  const r = await fetch(`${BASE}/crawl/status`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export function openCrawlStream(): EventSource {
  return new EventSource('/api/crawl/stream')
}

export async function postCrawlStart(mode: 'all' | 'missing' = 'all', releaseId?: string): Promise<{ started: boolean; running: boolean }> {
  const r = await fetch(`${BASE}/crawl/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode, release_id: releaseId }),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function postCrawlStop(): Promise<void> {
  await fetch(`${BASE}/crawl/stop`, { method: 'POST' })
}

export function openLogsStream(): EventSource {
  return new EventSource('/api/logs/stream')
}

export async function clearLogs(): Promise<void> {
  await fetch(`${BASE}/logs`, { method: 'DELETE' })
}

export async function listScreenshotSessions(): Promise<ScreenshotSession[]> {
  const r = await fetch(`${BASE}/screenshots`)
  if (!r.ok) throw new Error(await r.text())
  const data = await r.json()
  return data.sessions
}

export function screenshotUrl(path: string): string {
  return `${BASE}/screenshots/${path}`
}

export async function getAuthStatus(): Promise<{ active: boolean; active_site: string | null; has_state: boolean; state_mtime: number | null }> {
  const r = await fetch(`${BASE}/auth/status`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function startLogin(site_name: string, login_url: string): Promise<void> {
  const r = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ site_name, login_url }),
  })
  if (!r.ok) throw new Error(await r.text())
}

export async function finishLogin(): Promise<void> {
  const r = await fetch(`${BASE}/auth/done`, { method: 'POST' })
  if (!r.ok) throw new Error(await r.text())
}

export async function clearAuthState(): Promise<void> {
  const r = await fetch(`${BASE}/auth/state`, { method: 'DELETE' })
  if (!r.ok) throw new Error(await r.text())
}
