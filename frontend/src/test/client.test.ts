import { describe, it, expect, vi, beforeEach } from 'vitest'
import { postCrawlStart, postStockSyncStart, getUserSettings, saveUserSettings, logout, getStock, getStockArtists, getReleases, getArtists, postPlexMatchStart, refreshCollection, openCrawlStream, openLogsStream, importRecommendationsCsv, listInvites, createInvite, getUserHiddenCrawlers, postUserHiddenCrawlers, saveStockItem, unsaveStockItem, getStockStats, checkHealth, getQueueSummary, getQueueNext, getNotifications, getNotificationsUnread, markNotificationsRead } from '../api/client'

describe('crawl/user-settings client functions', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    class EventSourceMock {
      withCredentials: boolean
      close: () => void
      constructor(_url: string, options?: { withCredentials?: boolean }) {
        this.withCredentials = options?.withCredentials ?? false
        this.close = vi.fn()
      }
    }
    vi.stubGlobal('EventSource', EventSourceMock as any)
  })

  it('postCrawlStart returns enqueued count', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ enqueued: 3 }) })
    const result = await postCrawlStart('all')
    expect(result.enqueued).toBe(3)
  })

  it('checkHealth passes an AbortSignal, and resolves false when the request is aborted', async () => {
    let capturedSignal: AbortSignal | undefined
    fetchMock.mockImplementation((_url: string, init: RequestInit) => {
      capturedSignal = init.signal as AbortSignal
      // Simulates what a hung connection's timeout firing does to fetch() --
      // this is what a removed or miswired signal would fail to reproduce.
      return Promise.reject(new DOMException('The operation was aborted.', 'TimeoutError'))
    })

    await expect(checkHealth()).resolves.toBe(false)
    expect(capturedSignal).toBeInstanceOf(AbortSignal)
  })

  it('postStockSyncStart posts an empty crawler_id for a bulk call', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ started: true, running: true }) })
    await postStockSyncStart()
    expect(fetchMock.mock.calls[0][0]).toContain('/stock/sync/start')
    expect(fetchMock.mock.calls[0][1].method).toBe('POST')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({})
  })

  it('postStockSyncStart posts the given crawler_id for a single-store call', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ started: true, running: true }) })
    await postStockSyncStart(7)
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ crawler_id: 7 })
  })

  it('getUserSettings fetches /user-settings', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ anthropic_api_key: '', recommendation_item_limit: 300, plex_base_url: '', plex_token: '', plex_match_threshold: 90 }) })
    await getUserSettings()
    expect(fetchMock.mock.calls[0][0]).toContain('/user-settings')
  })

  it('saveUserSettings posts to /user-settings', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) })
    await saveUserSettings({ anthropic_api_key: 'sk-ant-test', recommendation_item_limit: 300, plex_base_url: '', plex_token: '', plex_match_threshold: 90 })
    expect(fetchMock.mock.calls[0][0]).toContain('/user-settings')
    expect(fetchMock.mock.calls[0][1].method).toBe('POST')
  })

  it('getUserHiddenCrawlers fetches /user-hidden-crawlers', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ hidden_crawler_ids: [3, 7] }) })
    const result = await getUserHiddenCrawlers()
    expect(fetchMock.mock.calls[0][0]).toContain('/user-hidden-crawlers')
    expect(result).toEqual([3, 7])
  })

  it('postUserHiddenCrawlers posts the full id list to /user-hidden-crawlers', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ ok: true }) })
    await postUserHiddenCrawlers([3, 7])
    expect(fetchMock.mock.calls[0][0]).toContain('/user-hidden-crawlers')
    expect(fetchMock.mock.calls[0][1].method).toBe('POST')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ hidden_crawler_ids: [3, 7] })
  })

  it('logout resolves on a successful response', async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 200, text: async () => '' })
    await expect(logout()).resolves.toBeUndefined()
  })

  it('logout rejects when the server responds with an error', async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 500, text: async () => 'Internal Server Error' })
    await expect(logout()).rejects.toThrow('Internal Server Error')
  })

  it('getStock includes hidden_crawler_ids when provided', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] }) })
    await getStock({ hiddenCrawlerIds: [3, 7] })
    expect(fetchMock.mock.calls[0][0]).toContain('hidden_crawler_ids=3%2C7')
  })

  it('getStock omits hidden_crawler_ids when the list is empty', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] }) })
    await getStock({ hiddenCrawlerIds: [] })
    expect(fetchMock.mock.calls[0][0]).not.toContain('hidden_crawler_ids')
  })

  it('getStockArtists includes hidden_crawler_ids when provided', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ artists: [] }) })
    await getStockArtists({ hiddenCrawlerIds: [3, 7] })
    expect(fetchMock.mock.calls[0][0]).toContain('hidden_crawler_ids=3%2C7')
  })

  it('maps libraryScope to the backend library_scope value', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] }) })
    await getStock({ libraryScope: 'wantlist' })
    expect(fetchMock.mock.calls[0][0]).toContain('library_scope=wishlist')

    await getStock({ libraryScope: 'collection' })
    expect(fetchMock.mock.calls[1][0]).toContain('library_scope=collection')

    await getStock({ libraryScope: 'all' })
    expect(fetchMock.mock.calls[2][0]).toContain('library_scope=all')

    await getStock({})
    expect(fetchMock.mock.calls[3][0]).not.toContain('library_scope')
  })

  it('getStockStats requests /stock/stats and serializes every filter it is given', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, sources: [] }) })
    await getStockStats({
      search: 'rob zombie', artist: 'Rob Zombie', libraryScope: 'wantlist',
      recommended: true, saved: true, overlapped: true, hiddenCrawlerIds: [3, 7],
    })
    const url = fetchMock.mock.calls[0][0] as string
    expect(url).toContain('/stock/stats?')
    // The names are the backend's, not the browser's: a rename on either side
    // has to break something, and the component tests mock this wrapper away.
    expect(url).toContain('search=rob+zombie')
    expect(url).toContain('artist=Rob+Zombie')
    expect(url).toContain('library_scope=wishlist')
    expect(url).toContain('recommended=true')
    expect(url).toContain('saved=true')
    expect(url).toContain('overlapped=true')
    expect(url).toContain('hidden_crawler_ids=3%2C7')
  })

  it('getStockStats omits every filter it is not given, rather than sending falsey ones', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, sources: [] }) })
    await getStockStats({ recommended: false, saved: false, overlapped: false, hiddenCrawlerIds: [] })
    const url = fetchMock.mock.calls[0][0] as string
    // A bare /stock/stats, not one carrying recommended=false: the backend
    // reads these as flags, and a sent false is a filter the user never set.
    expect(url).toMatch(/\/stock\/stats$/)
  })

  it('getStockStats sends no query string at all when called with no arguments', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, sources: [] }) })
    await getStockStats()
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/stock\/stats$/)
  })

  it('getStockArtists maps libraryScope to the backend value', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ artists: [] }) })
    await getStockArtists({ libraryScope: 'wantlist' })
    expect(fetchMock.mock.calls[0][0]).toContain('library_scope=wishlist')
  })

  it('getReleases includes unmatched when true', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, page: 1, per_page: 50, releases: [] }) })
    await getReleases({ unmatched: true })
    expect(fetchMock.mock.calls[0][0]).toContain('unmatched=true')
  })

  it('getReleases omits unmatched when false or omitted', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, page: 1, per_page: 50, releases: [] }) })
    await getReleases({})
    expect(fetchMock.mock.calls[0][0]).not.toContain('unmatched')
  })

  it('refreshCollection omits query params when called with no args', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ started: true, running: true }) })
    await refreshCollection()
    expect(fetchMock.mock.calls[0][0]).not.toContain('?')
  })

  it('refreshCollection includes scope=wishlist when scope is wantlist', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ started: true, running: true }) })
    await refreshCollection('all', 'wantlist')
    expect(fetchMock.mock.calls[0][0]).toContain('scope=wishlist')
    expect(fetchMock.mock.calls[0][0]).not.toContain('mode=')
  })

  it('refreshCollection includes both mode=new and scope=wishlist when scope is wantlist', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ started: true, running: true }) })
    await refreshCollection('new', 'wantlist')
    expect(fetchMock.mock.calls[0][0]).toContain('mode=new')
    expect(fetchMock.mock.calls[0][0]).toContain('scope=wishlist')
  })

  it('translates RecordScope values to the backend scope vocabulary', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, page: 1, per_page: 50, releases: [] }) })
    await getReleases({ scope: 'collection' })
    expect(fetchMock.mock.calls[0][0]).toContain('scope=discogs')

    await getReleases({ scope: 'wantlist' })
    expect(fetchMock.mock.calls[1][0]).toContain('scope=wishlist')
  })

  it('getArtists translates the wantlist scope to the backend value', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ artists: [] }) })
    await getArtists('wantlist')
    expect(fetchMock.mock.calls[0][0]).toContain('scope=wishlist')
  })

  it('postPlexMatchStart posts to /plex/match/start', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ started: true, running: true }) })
    const result = await postPlexMatchStart()
    expect(fetchMock.mock.calls[0][0]).toContain('/plex/match/start')
    expect(fetchMock.mock.calls[0][1].method).toBe('POST')
    expect(result.started).toBe(true)
  })

  it('apiFetch requests include credentials for cross-origin cookie auth', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) })
    await getUserSettings()
    expect(fetchMock.mock.calls[0][1].credentials).toBe('include')
  })

  it('openCrawlStream sets withCredentials for cross-origin cookie auth', () => {
    const es = openCrawlStream()
    expect(es.withCredentials).toBe(true)
    es.close()
  })

  it('openLogsStream sets withCredentials for cross-origin cookie auth', () => {
    const es = openLogsStream()
    expect(es.withCredentials).toBe(true)
    es.close()
  })

  it('BASE uses VITE_API_BASE_URL when set, for cross-origin API calls', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.tracktempest.com/api')
    vi.resetModules()
    const { getUserSettings: getUserSettingsWithBase } = await import('../api/client')
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) })
    await getUserSettingsWithBase()
    expect(fetchMock.mock.calls[0][0]).toBe('https://api.tracktempest.com/api/user-settings')
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it('BASE strips a trailing slash from VITE_API_BASE_URL to avoid double slashes', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.tracktempest.com/api/')
    vi.resetModules()
    const { getUserSettings: getUserSettingsWithBase } = await import('../api/client')
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) })
    await getUserSettingsWithBase()
    expect(fetchMock.mock.calls[0][0]).toBe('https://api.tracktempest.com/api/user-settings')
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it('importRecommendationsCsv posts the file as multipart without setting Content-Type', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        imported: 2, updated: 0, unchanged: 0, skipped: 0,
        errors: [], matched_stock_items: 1, running: false,
      }),
    })
    const file = new File(['artist,title\n'], 'recommendations.csv', { type: 'text/csv' })

    const result = await importRecommendationsCsv(file)

    expect(result.imported).toBe(2)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toContain('/stock/import')
    expect(init.method).toBe('POST')
    expect(init.body).toBeInstanceOf(FormData)
    expect((init.body as FormData).get('file')).toBe(file)
    // The browser must supply the multipart boundary; setting Content-Type by
    // hand omits it and the request fails to parse server-side.
    expect(new Headers(init.headers).has('Content-Type')).toBe(false)
  })

  it('listInvites fetches /auth/invites', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => [] })
    await listInvites()
    expect(fetchMock.mock.calls[0][0]).toContain('/auth/invites')
  })

  it('createInvite posts the note and returns the minted code', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ code: 'ABC123' }) })
    const result = await createInvite('for a friend')
    expect(fetchMock.mock.calls[0][0]).toContain('/auth/invites')
    expect(fetchMock.mock.calls[0][1].method).toBe('POST')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ note: 'for a friend' })
    expect(result).toEqual({ code: 'ABC123' })
  })

  it('createInvite sends a null note when none is given', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ code: 'XYZ789' }) })
    await createInvite()
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ note: null })
  })

  it('getStock sends include_comparisons=false only when asked to omit them', async () => {
    // Tile view is the only caller that passes false, and this serialization is
    // what keeps a Cost-sorted tile page from coming back mostly comparison
    // rows the grid discards. Absent and true must both stay off the URL, so
    // the backend default (include them) is what every other caller gets.
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] }) })
    await getStock({ includeComparisons: false })
    expect(fetchMock.mock.calls[0][0]).toContain('include_comparisons=false')

    await getStock({ includeComparisons: true })
    expect(fetchMock.mock.calls[1][0]).not.toContain('include_comparisons')

    await getStock({})
    expect(fetchMock.mock.calls[2][0]).not.toContain('include_comparisons')
  })

  it('getStock forwards saved=true when saved is set', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] }) })
    await getStock({ saved: true })
    expect(fetchMock.mock.calls[0][0]).toContain('saved=true')
  })

  it('getStock omits saved when unset', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] }) })
    await getStock({})
    expect(fetchMock.mock.calls[0][0]).not.toContain('saved=')
  })

  it('getStockArtists forwards saved=true', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ artists: [] }) })
    await getStockArtists({ saved: true })
    expect(fetchMock.mock.calls[0][0]).toContain('saved=true')
  })

  it('getStock forwards overlapped=true when overlapped is set', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] }) })
    await getStock({ overlapped: true })
    expect(fetchMock.mock.calls[0][0]).toContain('overlapped=true')
  })

  it('getStock omits overlapped when unset', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, row_total: 0, page: 1, per_page: 250, items: [] }) })
    await getStock({})
    expect(fetchMock.mock.calls[0][0]).not.toContain('overlapped=')
  })

  it('getStockArtists forwards overlapped=true', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ artists: [] }) })
    await getStockArtists({ overlapped: true })
    expect(fetchMock.mock.calls[0][0]).toContain('overlapped=true')
  })

  it('saveStockItem PUTs to /stock/saved/:item_key', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ saved: true }) })
    await saveStockItem('abc123')
    expect(fetchMock.mock.calls[0][0]).toContain('/stock/saved/abc123')
    expect(fetchMock.mock.calls[0][1].method).toBe('PUT')
  })

  it('unsaveStockItem DELETEs to /stock/saved/:item_key', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ saved: false }) })
    await unsaveStockItem('abc123')
    expect(fetchMock.mock.calls[0][0]).toContain('/stock/saved/abc123')
    expect(fetchMock.mock.calls[0][1].method).toBe('DELETE')
  })

  it('getNotifications GETs /notifications, forwarding an explicit limit', async () => {
    fetchMock.mockResolvedValue({
      ok: true, json: async () => ({ items: [], unread: 0, latest_id: null, last_read_id: 0 }),
    })
    await getNotifications()
    expect(fetchMock.mock.calls[0][0]).toContain('/notifications')
    expect(fetchMock.mock.calls[0][0]).not.toContain('limit')

    await getNotifications(10)
    expect(fetchMock.mock.calls[1][0]).toContain('/notifications?limit=10')

    // Forwarded rather than dropped: 0 is outside the endpoint's ge=1 range and
    // should be rejected there, not silently answered with the default page.
    await getNotifications(0)
    expect(fetchMock.mock.calls[2][0]).toContain('/notifications?limit=0')
  })

  it('getNotificationsUnread GETs the badge-only endpoint', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ unread: 2, latest_id: 9 }) })
    await expect(getNotificationsUnread()).resolves.toEqual({ unread: 2, latest_id: 9 })
    expect(fetchMock.mock.calls[0][0]).toContain('/notifications/unread')
  })

  it('markNotificationsRead POSTs the watermark', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ unread: 0, latest_id: 9 }) })
    await markNotificationsRead(9)
    expect(fetchMock.mock.calls[0][0]).toContain('/notifications/read')
    expect(fetchMock.mock.calls[0][1].method).toBe('POST')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ up_to_id: 9 })
    // Every mutating request needs it -- AuthMiddleware rejects a POST without.
    expect(new Headers(fetchMock.mock.calls[0][1].headers).get('X-Requested-With')).toBe('fetch')
  })
})

describe('queue client functions', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ items: [] }) })
    vi.stubGlobal('fetch', fetchMock)
  })

  // QueueView holds at most one summary in flight, so a request that never
  // settles would wedge its polling loop for good -- last snapshot on screen,
  // no stale warning, no recovery.
  it('gives the summary request a deadline', async () => {
    await getQueueSummary()
    const [, init] = fetchMock.mock.calls[0]
    expect(init.signal).toBeInstanceOf(AbortSignal)
  })

  it('gives the next-up request a deadline', async () => {
    await getQueueNext(1)
    const [, init] = fetchMock.mock.calls[0]
    expect(init.signal).toBeInstanceOf(AbortSignal)
  })

  // The view's own guard is component-local, so it cannot survive the Queue tab
  // being unmounted. Coalescing here is what stops a leave/reopen during a slow
  // report issuing a second repeatable-read transaction alongside the first.
  it('coalesces concurrent summary requests into one', async () => {
    let release: (v: unknown) => void = () => {}
    fetchMock.mockReturnValue(new Promise((r) => {
      release = () => r({ ok: true, status: 200, json: async () => ({}) })
    }))

    const a = getQueueSummary()
    const b = getQueueSummary()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(a).toBe(b)

    release(null)
    await a
  })

  it('releases the coalescing slot once a summary settles', async () => {
    await getQueueSummary()
    await getQueueSummary()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('coalesces concurrent next-up requests for the same crawler', async () => {
    let release: (v: unknown) => void = () => {}
    fetchMock.mockReturnValue(new Promise((r) => {
      release = () => r({ ok: true, status: 200, json: async () => ({ items: [] }) })
    }))

    const a = getQueueNext(1)
    const b = getQueueNext(1)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(a).toBe(b)

    release(null)
    await a
  })

  it('does not coalesce next-up requests for different crawlers', async () => {
    let release: (v: unknown) => void = () => {}
    const pending = new Promise((r) => {
      release = () => r({ ok: true, status: 200, json: async () => ({ items: [] }) })
    })
    fetchMock.mockReturnValue(pending)

    const a = getQueueNext(1)
    const b = getQueueNext(2)
    // Different crawlers are different reports; sharing one would be wrong.
    expect(fetchMock).toHaveBeenCalledTimes(2)

    // Settled before the test ends: the coalescing map is module state, so
    // leaving an unresolved entry behind hands it to the next test.
    release(null)
    await Promise.all([a, b])
  })

  it('releases the next-up slot when a request fails', async () => {
    fetchMock.mockRejectedValueOnce(new Error('boom'))
    await expect(getQueueNext(1)).rejects.toThrow('boom')
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({ items: [] }) })
    await expect(getQueueNext(1)).resolves.toEqual([])
  })

  it('releases the coalescing slot when a summary fails', async () => {
    fetchMock.mockRejectedValueOnce(new Error('boom'))
    await expect(getQueueSummary()).rejects.toThrow('boom')
    // A rejection must not wedge every later caller on a dead promise.
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({}) })
    await expect(getQueueSummary()).resolves.toBeDefined()
  })
})
