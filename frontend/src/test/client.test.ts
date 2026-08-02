import { describe, it, expect, vi, beforeEach } from 'vitest'
import { postCrawlStart, getUserSettings, saveUserSettings, logout } from '../api/client'

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
})
