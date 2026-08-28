import type {
  ReleasesResponse, Crawler, Settings, UserSettings, SortField, SortOrder, CrawlStatus, CollectionStatus, ScreenshotSession,
  AuthStatus, RecordScope, StockResponse, StockSortField, LibraryScope, RecommendationImportResult, Invite,
  QueueSummary, QueueNextItem, NotificationsResponse, NotificationsUnread,
} from './types'

const BASE = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/+$/, '')

// The frontend says "collection"/"wantlist"; the /releases and /artists
// endpoints still say "discogs"/"wishlist". SSE events carry the API's
// vocabulary too (CrawlEvent.scope, wishlist_synced) and are translated
// where they're rendered, in App.tsx.
const RECORD_SCOPE_PARAM: Record<RecordScope, 'discogs' | 'wishlist'> = {
  collection: 'discogs',
  wantlist: 'wishlist',
}

const LIBRARY_SCOPE_PARAM: Record<LibraryScope, 'collection' | 'wishlist' | 'all'> = {
  collection: 'collection',
  wantlist: 'wishlist',
  all: 'all',
}

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
    const r = await apiFetch('/health', { signal: AbortSignal.timeout(4000) })
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

export async function refreshCollection(mode?: 'all' | 'new', scope?: 'all' | 'wantlist'): Promise<{ started: boolean; running: boolean }> {
  const q = new URLSearchParams()
  if (mode === 'new') q.set('mode', 'new')
  if (scope === 'wantlist') q.set('scope', RECORD_SCOPE_PARAM.wantlist)
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
  if (params.scope) q.set('scope', RECORD_SCOPE_PARAM[params.scope])
  if (params.unmatched) q.set('unmatched', 'true')
  const r = await apiFetch(`/releases?${q}`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function getArtists(scope?: RecordScope): Promise<string[]> {
  const q = new URLSearchParams()
  if (scope) q.set('scope', RECORD_SCOPE_PARAM[scope])
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

export async function getUserHiddenCrawlers(): Promise<number[]> {
  const r = await apiFetch('/user-hidden-crawlers')
  if (!r.ok) throw new Error(await r.text())
  const data = await r.json()
  return data.hidden_crawler_ids
}

export async function postUserHiddenCrawlers(hiddenCrawlerIds: number[]): Promise<void> {
  const r = await apiFetch('/user-hidden-crawlers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hidden_crawler_ids: hiddenCrawlerIds }),
  })
  if (!r.ok) throw new Error(await r.text())
}

export async function setCrawlerEnabled(id: number, enabled: boolean): Promise<{ ok: boolean; discarded: number }> {
  const r = await apiFetch(`/crawlers/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
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
  libraryScope?: LibraryScope
  recommended?: boolean
  saved?: boolean
  overlapped?: boolean
  hiddenCrawlerIds?: number[]
}): Promise<StockResponse> {
  const q = new URLSearchParams()
  if (params.search) q.set('search', params.search)
  if (params.artist) q.set('artist', params.artist)
  if (params.sort) q.set('sort', params.sort)
  if (params.order) q.set('order', params.order)
  if (params.page) q.set('page', String(params.page))
  if (params.per_page) q.set('per_page', String(params.per_page))
  if (params.libraryScope) q.set('library_scope', LIBRARY_SCOPE_PARAM[params.libraryScope])
  if (params.recommended) q.set('recommended', 'true')
  if (params.saved) q.set('saved', 'true')
  if (params.overlapped) q.set('overlapped', 'true')
  if (params.hiddenCrawlerIds?.length) q.set('hidden_crawler_ids', params.hiddenCrawlerIds.join(','))
  const r = await apiFetch(`/stock?${q}`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

// A named options object rather than getStock's sibling list of positional
// booleans: every filter this takes is a bare boolean, so a call site read
// `(undefined, false, [], false, true)` with nothing to say which `true` that
// was, and each new filter shifted every existing caller.
export async function getStockArtists(params: {
  libraryScope?: LibraryScope
  recommended?: boolean
  saved?: boolean
  overlapped?: boolean
  hiddenCrawlerIds?: number[]
} = {}): Promise<string[]> {
  const q = new URLSearchParams()
  if (params.libraryScope) q.set('library_scope', LIBRARY_SCOPE_PARAM[params.libraryScope])
  if (params.recommended) q.set('recommended', 'true')
  if (params.saved) q.set('saved', 'true')
  if (params.overlapped) q.set('overlapped', 'true')
  if (params.hiddenCrawlerIds?.length) q.set('hidden_crawler_ids', params.hiddenCrawlerIds.join(','))
  const qs = q.toString() ? `?${q}` : ''
  const r = await apiFetch(`/stock/artists${qs}`)
  if (!r.ok) throw new Error(await r.text())
  const data = await r.json()
  return data.artists
}

export async function saveStockItem(itemKey: string): Promise<{ saved: boolean }> {
  const r = await apiFetch(`/stock/saved/${encodeURIComponent(itemKey)}`, { method: 'PUT' })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function unsaveStockItem(itemKey: string): Promise<{ saved: boolean }> {
  const r = await apiFetch(`/stock/saved/${encodeURIComponent(itemKey)}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export interface StockSyncStartResult {
  started: boolean
  running: boolean
  // True when the sync holding the lock belongs to another Machine. Its source
  // and timings live in that process's memory and are null here, so this flag
  // is the only thing separating "running elsewhere, details unknowable" from
  // "running here but not yet past its first crawler."
  on_another_instance: boolean
  source: string | null
  elapsed_seconds: number | null
  source_elapsed_seconds: number | null
}

export async function postStockSyncStart(crawlerId?: number): Promise<StockSyncStartResult> {
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

export async function getPriceStatus(): Promise<{ any_price_paid: boolean }> {
  const r = await apiFetch('/collection/price-status')
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

export async function importRecommendationsCsv(file: File): Promise<RecommendationImportResult> {
  const body = new FormData()
  body.append('file', file)
  // Content-Type is deliberately unset: the browser adds it with the
  // multipart boundary, which a hand-set header would omit.
  const r = await apiFetch('/stock/import', { method: 'POST', body })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
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

export async function listInvites(): Promise<Invite[]> {
  const r = await apiFetch('/auth/invites')
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function createInvite(note?: string): Promise<{ code: string }> {
  const r = await apiFetch('/auth/invites', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ note: note || null }),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
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

// Both carry a deadline. QueueView holds at most one summary in flight, so a
// request that never settles would wedge its polling loop for good -- the last
// snapshot left on screen, no stale warning, no recovery. A timeout turns that
// into an ordinary rejection the view already renders, and the loop continues.
// Same mechanism checkHealth uses.
const QUEUE_TIMEOUT_MS = 20_000

// Coalesced at module scope, not per component. QueueView's own guard is
// component-local, so it cannot survive the view being unmounted -- and the
// Queue tab is mounted only while it is active, so leaving and reopening it
// during a slow report built a fresh instance that immediately issued another.
// Same escape hatch the timer and the hide/show path had; this closes the class
// rather than a third instance of it. A summary is a REPEATABLE READ
// transaction on the pool the crawl workers claim through, so overlapping them
// is the one thing this request must never do.
let summaryInFlight: Promise<QueueSummary> | null = null

export function getQueueSummary(): Promise<QueueSummary> {
  if (summaryInFlight) return summaryInFlight
  summaryInFlight = (async () => {
    try {
      const r = await apiFetch('/queue/summary', { signal: AbortSignal.timeout(QUEUE_TIMEOUT_MS) })
      if (!r.ok) throw new Error(await r.text())
      return await r.json()
    } finally {
      // Cleared here rather than by chaining onto this promise: a trailing
      // .finally() settles a microtask after an awaiter resumes, so a caller
      // that awaited and immediately called again would be handed the spent
      // slot. Inside the function it is already null when this promise
      // settles -- and it settles either way, so a rejection cannot wedge
      // every later caller.
      summaryInFlight = null
    }
  })()
  return summaryInFlight
}

// Coalesced per (crawler, limit) for the same reason as the summary. The view
// re-runs this on every poll -- the effect depends on the summary's timestamp --
// so a next-up query slower than the poll interval would otherwise start
// another alongside it, and a tab unmount/remount is the same escape again.
const nextInFlight = new Map<string, Promise<QueueNextItem[]>>()

export function getQueueNext(crawlerId: number, limit = 25): Promise<QueueNextItem[]> {
  const key = `${crawlerId}:${limit}`
  const existing = nextInFlight.get(key)
  if (existing) return existing
  const request = (async () => {
    try {
      const r = await apiFetch(`/queue/crawlers/${crawlerId}/next?limit=${limit}`, {
        signal: AbortSignal.timeout(QUEUE_TIMEOUT_MS),
      })
      if (!r.ok) throw new Error(await r.text())
      return (await r.json()).items
    } finally {
      // Cleared inside, not by chaining onto the promise -- a trailing
      // .finally() settles a microtask after an awaiter resumes and would hand
      // the next caller a spent entry. Same reason as the summary above.
      nextInFlight.delete(key)
    }
  })()
  nextInFlight.set(key, request)
  return request
}

export async function getNotifications(limit?: number): Promise<NotificationsResponse> {
  const qs = limit ? `?limit=${limit}` : ''
  const r = await apiFetch(`/notifications${qs}`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

// Separate from getNotifications so the header's badge -- refetched on every SSE
// generation tick, on whatever screen the user is on -- doesn't pull rows
// nothing is going to render.
export async function getNotificationsUnread(): Promise<NotificationsUnread> {
  const r = await apiFetch('/notifications/unread')
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function markNotificationsRead(upToId: number): Promise<NotificationsUnread> {
  const r = await apiFetch('/notifications/read', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ up_to_id: upToId }),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
