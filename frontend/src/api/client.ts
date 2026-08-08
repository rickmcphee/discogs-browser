import type {
  ReleasesResponse, Crawler, Settings, UserSettings, SortField, SortOrder, CrawlStatus, CollectionStatus, ScreenshotSession,
  AuthStatus, RecordScope, StockResponse, StockSortField,
} from './types'

const BASE = import.meta.env.VITE_API_BASE_URL || '/api'

let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: () => void) { onUnauthorized = fn }

async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('X-Requested-With', 'fetch')
  const r = await fetch(`${BASE}${path}`, { ...init, headers, credentials: 'include' })
  if (r.status === 401) {
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

export async function refreshCollection(mode?: 'all' | 'new', scope?: 'all' | 'wishlist'): Promise<{ started: boolean; running: boolean }> {
  const q = new URLSearchParams()
  if (mode === 'new') q.set('mode', 'new')
  if (scope === 'wishlist') q.set('scope', 'wishlist')
  const url = q.toString() ? `/collection/refresh?${q}` : '/collection/refresh'
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
  unmatched?: boolean
}): Promise<ReleasesResponse> {
  const q = new URLSearchParams()
  if (params.search) q.set('search', params.search)
  if (params.artist) q.set('artist', params.artist)
  if (params.sort) q.set('sort', params.sort)
  if (params.order) q.set('order', params.order)
  if (params.page) q.set('page', String(params.page))
  if (params.per_page) q.set('per_page', String(params.per_page))
  if (params.scope) q.set('scope', params.scope)
  if (params.unmatched) q.set('unmatched', 'true')
  const r = await apiFetch(`/releases?${q}`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function getArtists(scope?: RecordScope): Promise<string[]> {
  const q = new URLSearchParams()
  if (scope) q.set('scope', scope)
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

export async function getUserSettings(): Promise<UserSettings> {
  const r = await apiFetch('/user-settings')
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function saveUserSettings(settings: UserSettings): Promise<void> {
  const r = await apiFetch('/user-settings', {
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
  return new EventSource(`${BASE}/crawl/stream`, { withCredentials: true })
}

export async function postCrawlStart(mode: 'all' | 'missing' = 'all', releaseId?: string): Promise<{ enqueued: number }> {
  const r = await apiFetch('/crawl/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode, release_id: releaseId }),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
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
  hiddenCrawlerIds?: number[]
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
  if (params.hiddenCrawlerIds?.length) q.set('hidden_crawler_ids', params.hiddenCrawlerIds.join(','))
  const r = await apiFetch(`/stock?${q}`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function getStockArtists(overlapping?: boolean, recommended?: boolean, hiddenCrawlerIds?: number[]): Promise<string[]> {
  const q = new URLSearchParams()
  if (overlapping) q.set('overlapping', 'true')
  if (recommended) q.set('recommended', 'true')
  if (hiddenCrawlerIds?.length) q.set('hidden_crawler_ids', hiddenCrawlerIds.join(','))
  const qs = q.toString() ? `?${q}` : ''
  const r = await apiFetch(`/stock/artists${qs}`)
  if (!r.ok) throw new Error(await r.text())
  const data = await r.json()
  return data.artists
}

export async function postStockSyncStart(crawlerId?: number): Promise<{ started: boolean; running: boolean }> {
  const r = await apiFetch('/stock/sync/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ crawler_id: crawlerId }),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function postPlexMatchStart(): Promise<{ started: boolean; running: boolean }> {
  const r = await apiFetch('/plex/match/start', { method: 'POST' })
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

export function openLogsStream(levels?: string[]): EventSource {
  const qs = levels && levels.length
    ? `?levels=${encodeURIComponent(levels.join(','))}`
    : ''
  return new EventSource(`${BASE}/logs/stream${qs}`, { withCredentials: true })
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

export async function getAuthStatus(): Promise<AuthStatus> {
  const r = await apiFetch('/auth/status')
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export function discogsLoginUrl(): string {
  return `${BASE}/auth/discogs/start`
}

export async function redeemInvite(signupToken: string, inviteCode: string): Promise<void> {
  const r = await apiFetch('/auth/redeem-invite', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ signup_token: signupToken, invite_code: inviteCode }),
  })
  if (!r.ok) throw new Error(await r.text())
}

export async function logout(): Promise<void> {
  const r = await apiFetch('/auth/logout', { method: 'POST' })
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
