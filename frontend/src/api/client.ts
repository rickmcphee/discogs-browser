import type {
  ReleasesResponse, Crawler, Settings, SortField, SortOrder, CrawlStatus, CollectionStatus, ScreenshotSession,
  AuthState, SetupResponse, RecordScope, StockResponse, StockSortField,
} from './types'

const BASE = '/api'

let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: () => void) { onUnauthorized = fn }

async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('X-Requested-With', 'fetch')
  const r = await fetch(`${BASE}${path}`, { ...init, headers })
  if (r.status === 401 && path !== '/auth/status' && path !== '/auth/login') {
    onUnauthorized?.()
  }
  return r
}

export async function checkHealth(): Promise<boolean> {
  try {
    const r = await apiFetch('/health')
    // Any non-5xx means the backend is reachable (5xx = nginx gateway error)
    return r.status < 500
  } catch {
    return false
  }
}

export async function getCollectionStatus(): Promise<CollectionStatus> {
  const r = await apiFetch('/collection/status')
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function refreshCollection(mode?: 'all' | 'new'): Promise<{ started: boolean; running: boolean }> {
  const url = mode === 'new' ? '/collection/refresh?mode=new' : '/collection/refresh'
  const r = await apiFetch(url, { method: 'POST' })
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
  scope?: RecordScope
}): Promise<ReleasesResponse> {
  const q = new URLSearchParams()
  if (params.search) q.set('search', params.search)
  if (params.artist) q.set('artist', params.artist)
  if (params.sort) q.set('sort', params.sort)
  if (params.order) q.set('order', params.order)
  if (params.page) q.set('page', String(params.page))
  if (params.per_page) q.set('per_page', String(params.per_page))
  if (params.scope) q.set('scope', params.scope)
  const r = await apiFetch(`/releases?${q}`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function getArtists(scope?: RecordScope): Promise<string[]> {
  const q = scope ? `?scope=${scope}` : ''
  const r = await apiFetch(`/artists${q}`)
  if (!r.ok) throw new Error(await r.text())
  const data = await r.json()
  return data.artists
}

export async function getCrawlers(): Promise<Crawler[]> {
  const r = await apiFetch('/crawlers')
  if (!r.ok) throw new Error(await r.text())
  const data = await r.json()
  return data.crawlers
}

export async function getSettings(): Promise<Settings> {
  const r = await apiFetch('/settings')
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function saveSettings(settings: Settings): Promise<void> {
  const r = await apiFetch('/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  })
  if (!r.ok) throw new Error(await r.text())
}

export async function setCrawlerEnabled(id: number, enabled: boolean): Promise<void> {
  const r = await apiFetch(`/crawlers/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!r.ok) throw new Error(await r.text())
}

export async function getCrawlStatus(): Promise<CrawlStatus> {
  const r = await apiFetch('/crawl/status')
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export function openCrawlStream(): EventSource {
  return new EventSource('/api/crawl/stream')
}

export async function postCrawlStart(mode: 'all' | 'missing' = 'all', releaseId?: string): Promise<{ started: boolean; running: boolean }> {
  const r = await apiFetch('/crawl/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode, release_id: releaseId }),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function postCrawlStop(): Promise<void> {
  await apiFetch('/crawl/stop', { method: 'POST' })
}

export async function getStock(params: {
  search?: string
  artist?: string
  sort?: StockSortField
  order?: SortOrder
  page?: number
  per_page?: number
}): Promise<StockResponse> {
  const q = new URLSearchParams()
  if (params.search) q.set('search', params.search)
  if (params.artist) q.set('artist', params.artist)
  if (params.sort) q.set('sort', params.sort)
  if (params.order) q.set('order', params.order)
  if (params.page) q.set('page', String(params.page))
  if (params.per_page) q.set('per_page', String(params.per_page))
  const r = await apiFetch(`/stock?${q}`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function getStockArtists(): Promise<string[]> {
  const r = await apiFetch('/stock/artists')
  if (!r.ok) throw new Error(await r.text())
  const data = await r.json()
  return data.artists
}

export async function postStockSyncStart(): Promise<{ started: boolean; running: boolean }> {
  const r = await apiFetch('/stock/sync/start', { method: 'POST' })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export function openLogsStream(): EventSource {
  return new EventSource('/api/logs/stream')
}

export async function clearLogs(): Promise<void> {
  await apiFetch('/logs', { method: 'DELETE' })
}

export async function listScreenshotSessions(): Promise<ScreenshotSession[]> {
  const r = await apiFetch('/screenshots')
  if (!r.ok) throw new Error(await r.text())
  const data = await r.json()
  return data.sessions
}

export function screenshotUrl(path: string): string {
  return `${BASE}/screenshots/${path}`
}

export async function getAuthStatus(): Promise<{ active: boolean; active_site: string | null; has_state: boolean; state_mtime: number | null }> {
  const r = await apiFetch('/crawler-auth/status')
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function startLogin(site_name: string, login_url: string): Promise<void> {
  const r = await apiFetch('/crawler-auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ site_name, login_url }),
  })
  if (!r.ok) throw new Error(await r.text())
}

export async function finishLogin(): Promise<void> {
  const r = await apiFetch('/crawler-auth/done', { method: 'POST' })
  if (!r.ok) throw new Error(await r.text())
}

export async function clearAuthState(): Promise<void> {
  const r = await apiFetch('/crawler-auth/state', { method: 'DELETE' })
  if (!r.ok) throw new Error(await r.text())
}

export async function getAuthState(): Promise<AuthState> {
  const r = await apiFetch('/auth/status')
  if (!r.ok) throw new Error(await r.text())
  return (await r.json()).state
}

export async function setupOwner(bootstrapToken: string, password: string): Promise<SetupResponse> {
  const r = await apiFetch('/auth/setup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ bootstrap_token: bootstrapToken, password }),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function verifySetup(code: string): Promise<string[]> {
  const r = await apiFetch('/auth/setup/verify', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  })
  if (!r.ok) throw new Error(await r.text())
  return (await r.json()).recovery_codes
}

export async function login(password: string, code: string): Promise<void> {
  const r = await apiFetch('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password, code }),
  })
  if (!r.ok) throw new Error(await r.text())
}

export async function logout(): Promise<void> {
  await apiFetch('/auth/logout', { method: 'POST' })
}

export async function changePassword(currentPassword: string, newPassword: string, code: string): Promise<void> {
  const r = await apiFetch('/auth/change-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword, code }),
  })
  if (!r.ok) throw new Error(await r.text())
}
