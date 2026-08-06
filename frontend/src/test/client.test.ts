import { describe, it, expect, vi, beforeEach } from 'vitest'
import { postCrawlStart, getUserSettings, saveUserSettings, logout, getStock, getStockArtists, getReleases, postPlexMatchStart } from '../api/client'

describe('crawl/user-settings client functions', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })

  it('postCrawlStart returns enqueued count', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ enqueued: 3 }) })
    const result = await postCrawlStart('all')
    expect(result.enqueued).toBe(3)
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
    await getStockArtists(false, false, [3, 7])
    expect(fetchMock.mock.calls[0][0]).toContain('hidden_crawler_ids=3%2C7')
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

  it('postPlexMatchStart posts to /plex/match/start', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ started: true, running: true }) })
    const result = await postPlexMatchStart()
    expect(fetchMock.mock.calls[0][0]).toContain('/plex/match/start')
    expect(fetchMock.mock.calls[0][1].method).toBe('POST')
    expect(result.started).toBe(true)
  })
})
