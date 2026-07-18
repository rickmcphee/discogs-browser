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
  no_plex?: boolean
}): Promise<ReleasesResponse> {
  const q = new URLSearchParams()
  if (params.search) q.set('search', params.search)
  if (params.artist) q.set('artist', params.artist)
  if (params.sort) q.set('sort', params.sort)
  if (params.order) q.set('order', params.order)
  if (params.page) q.set('page', String(params.page))
  if (params.per_page) q.set('per_page', String(params.per_page))
  if (params.scope) q.set('scope', params.scope)
  if (params.no_plex) q.set('no_plex', 'true')
  const r = await apiFetch(`/releases?${q}`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function getArtists(scope?: RecordScope, noPlex?: boolean): Promise<string[]> {
  const q = new URLSearchParams()
  if (scope) q.set('scope', scope)
  if (noPlex) q.set('no_plex', 'true')
  const qs = q.toString() ? `?${q}` : ''
  const r = await apiFetch(`/artists${qs}`)
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
  overlapping?: boolean
  recommended?: boolean
}): Promise<StockResponse> {
  const q = new URLSearchParams()
  if (params.search) q.set('search', params.search)
  if (params.artist) q.set('artist', params.artist)
  if (params.sort) q.set('sort', params.sort)
  if (params.order) q.set('order', params.order)
  if (params.page) q.set('page', String(params.page))
  if (params.per_page) q.set('per_page', String(params.per_page))
  if (params.overlapping) q.set('overlapping', 'true')
  if (params.recommended) q.set('recommended', 'true')
  const r = await apiFetch(`/stock?${q}`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function getStockArtists(overlapping?: boolean, recommended?: boolean): Promise<string[]> {
  const q = new URLSearchParams()
  if (overlapping) q.set('overlapping', 'true')
  if (recommended) q.set('recommended', 'true')
  const qs = q.toString() ? `?${q}` : ''
  const r = await apiFetch(`/stock/artists${qs}`)
  if (!r.ok) throw new Error(await r.text())
  const data = await r.json()
  return data.artists
}

export async function postStockSyncStart(): Promise<{ started: boolean; running: boolean }> {
  const r = await apiFetch('/stock/sync/start', { method: 'POST' })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function postJudgmentStart(): Promise<{ started: boolean; running: boolean }> {
  const r = await apiFetch('/stock/judge/start', { method: 'POST' })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function getJudgmentStatus(): Promise<{ any_judged: boolean }> {
  const r = await apiFetch('/stock/judge/status')
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function clearJudgments(): Promise<{ cleared: boolean; running: boolean; count?: number }> {
  const r = await apiFetch('/stock/judge/clear', { method: 'POST' })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function exportRecommendationsCsv(): Promise<Blob> {
  const r = await apiFetch('/stock/export')
  if (!r.ok) throw new Error(await r.text())
  return r.blob()
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

export async function hasAvatar(): Promise<boolean> {
  const r = await apiFetch('/auth/avatar')
  if (r.status === 404) return false
  if (!r.ok) throw new Error(await r.text())
  return true
}

export async function uploadAvatar(file: File): Promise<void> {
  const body = new FormData()
  body.append('file', file)
  const r = await apiFetch('/auth/avatar', { method: 'POST', body })
  if (!r.ok) throw new Error(await r.text())
}

export async function deleteAvatar(): Promise<void> {
  const r = await apiFetch('/auth/avatar', { method: 'DELETE' })
  if (!r.ok) throw new Error(await r.text())
}

export function avatarUrl(version: number): string {
  return `${BASE}/auth/avatar?v=${version}`
}
