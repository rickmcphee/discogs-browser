import { describe, it, expect, vi, beforeEach } from 'vitest'
import { postCrawlStart, postStockSyncStart, getUserSettings, saveUserSettings, logout, getStock, getStockArtists, getReleases, getArtists, postPlexMatchStart, refreshCollection, openCrawlStream, openLogsStream } from '../api/client'

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

  it('logout resolves on a successful response', async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 200, text: async () => '' })
    await expect(logout()).resolves.toBeUndefined()
  })

  it('logout rejects when the server responds with an error', async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 500, text: async () => 'Internal Server Error' })
    await expect(logout()).rejects.toThrow('Internal Server Error')
  })

  it('getStock includes hidden_crawler_ids when provided', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, page: 1, per_page: 250, items: [] }) })
    await getStock({ hiddenCrawlerIds: [3, 7] })
    expect(fetchMock.mock.calls[0][0]).toContain('hidden_crawler_ids=3%2C7')
  })

  it('getStock omits hidden_crawler_ids when the list is empty', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, page: 1, per_page: 250, items: [] }) })
    await getStock({ hiddenCrawlerIds: [] })
    expect(fetchMock.mock.calls[0][0]).not.toContain('hidden_crawler_ids')
  })

  it('getStockArtists includes hidden_crawler_ids when provided', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ artists: [] }) })
    await getStockArtists(undefined, false, [3, 7])
    expect(fetchMock.mock.calls[0][0]).toContain('hidden_crawler_ids=3%2C7')
  })

  it('maps libraryScope to the backend library_scope value', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ total: 0, page: 1, per_page: 250, items: [] }) })
    await getStock({ libraryScope: 'wantlist' })
    expect(fetchMock.mock.calls[0][0]).toContain('library_scope=wishlist')

    await getStock({ libraryScope: 'collection' })
    expect(fetchMock.mock.calls[1][0]).toContain('library_scope=collection')

    await getStock({ libraryScope: 'all' })
    expect(fetchMock.mock.calls[2][0]).toContain('library_scope=all')

    await getStock({})
    expect(fetchMock.mock.calls[3][0]).not.toContain('library_scope')
  })

  it('getStockArtists maps libraryScope to the backend value', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ artists: [] }) })
    await getStockArtists('wantlist')
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
})
