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

describe('refetch on sync progress', () => {
  it('reloads releases each time syncGeneration increments', async () => {
    const { rerender } = render(
      <RecordBrowser scope="collection" syncGeneration={0} />
    )
    await waitFor(() => expect(getReleases).toHaveBeenCalledTimes(1))

    rerender(
      <RecordBrowser scope="collection" syncGeneration={1} />
    )
    await waitFor(() => expect(getReleases).toHaveBeenCalledTimes(2))

    rerender(
      <RecordBrowser scope="collection" syncGeneration={2} />
    )
    await waitFor(() => expect(getReleases).toHaveBeenCalledTimes(3))
  })

  it('does not reload again when syncGeneration stays the same', async () => {
    const { rerender } = render(
      <RecordBrowser scope="collection" syncGeneration={0} />
    )
    await waitFor(() => expect(getReleases).toHaveBeenCalledTimes(1))

    rerender(
      <RecordBrowser scope="collection" syncGeneration={1} />
    )
    await waitFor(() => expect(getReleases).toHaveBeenCalledTimes(2))

    rerender(
      <RecordBrowser scope="collection" syncGeneration={1} />
    )
    await waitFor(() => expect(getReleases).toHaveBeenCalledTimes(2))
  })
})
