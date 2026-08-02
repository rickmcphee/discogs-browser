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
      <RecordBrowser scope="collection" onRefreshPrices={() => {}} syncGeneration={0} />
    )
    await waitFor(() => expect(getReleases).toHaveBeenCalledTimes(1))

    rerender(
      <RecordBrowser scope="collection" onRefreshPrices={() => {}} syncGeneration={1} />
    )
    await waitFor(() => expect(getReleases).toHaveBeenCalledTimes(2))

    rerender(
      <RecordBrowser scope="collection" onRefreshPrices={() => {}} syncGeneration={2} />
    )
    await waitFor(() => expect(getReleases).toHaveBeenCalledTimes(3))
  })

  it('does not reload again when syncGeneration stays the same', async () => {
    const { rerender } = render(
      <RecordBrowser scope="collection" onRefreshPrices={() => {}} syncGeneration={0} />
    )
    await waitFor(() => expect(getReleases).toHaveBeenCalledTimes(1))

    rerender(
      <RecordBrowser scope="collection" onRefreshPrices={() => {}} syncGeneration={1} />
    )
    await waitFor(() => expect(getReleases).toHaveBeenCalledTimes(2))

    rerender(
      <RecordBrowser scope="collection" onRefreshPrices={() => {}} syncGeneration={1} />
    )
    await waitFor(() => expect(getReleases).toHaveBeenCalledTimes(2))
  })
})

describe('active-gated refetch (avoids duplicate refetch for a hidden view)', () => {
  it('does not reload on a syncGeneration tick while inactive', async () => {
    const { rerender } = render(
      <RecordBrowser scope="wishlist" onRefreshPrices={() => {}} syncGeneration={0} active={false} />
    )
    await waitFor(() => expect(getReleases).toHaveBeenCalledTimes(1))

    rerender(
      <RecordBrowser scope="wishlist" onRefreshPrices={() => {}} syncGeneration={1} active={false} />
    )
    await new Promise((r) => setTimeout(r, 10))
    expect(getReleases).toHaveBeenCalledTimes(1)
  })

  it('reloads once when becoming active, catching up on generations missed while inactive', async () => {
    const { rerender } = render(
      <RecordBrowser scope="wishlist" onRefreshPrices={() => {}} syncGeneration={1} active={false} />
    )
    await waitFor(() => expect(getReleases).toHaveBeenCalledTimes(1))

    rerender(
      <RecordBrowser scope="wishlist" onRefreshPrices={() => {}} syncGeneration={1} active={true} />
    )
    await waitFor(() => expect(getReleases).toHaveBeenCalledTimes(2))
  })
})
