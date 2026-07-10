import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import RecordBrowser from '../views/RecordBrowser'
import type { Release } from '../api/types'

const { getReleases } = vi.hoisted(() => ({
  getReleases: vi.fn().mockResolvedValue({ total: 0, page: 1, per_page: 50, releases: [] as Release[] }),
}))

vi.mock('../api/client', () => ({
  getReleases,
  getArtists: vi.fn().mockResolvedValue([]),
}))

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('localStorage', {
    getItem: () => null,
    setItem: () => {},
  })
})

describe('refetch on sync completion', () => {
  it('reloads releases when syncing transitions from true to false', async () => {
    const { rerender } = render(
      <RecordBrowser scope="collection" onRefreshPrices={() => {}} syncing={true} />
    )
    await waitFor(() => expect(getReleases).toHaveBeenCalledTimes(1))

    rerender(
      <RecordBrowser scope="collection" onRefreshPrices={() => {}} syncing={false} />
    )
    await waitFor(() => expect(getReleases).toHaveBeenCalledTimes(2))
  })

  it('does not reload again while syncing stays false', async () => {
    const { rerender } = render(
      <RecordBrowser scope="collection" onRefreshPrices={() => {}} syncing={false} />
    )
    await waitFor(() => expect(getReleases).toHaveBeenCalledTimes(1))

    rerender(
      <RecordBrowser scope="collection" onRefreshPrices={() => {}} syncing={false} />
    )
    await waitFor(() => expect(getReleases).toHaveBeenCalledTimes(1))
  })
})
