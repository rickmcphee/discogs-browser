import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import StockBrowser from '../views/StockBrowser'

const { getStock, getStockArtists } = vi.hoisted(() => ({
  getStock: vi.fn().mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] }),
  getStockArtists: vi.fn().mockResolvedValue([]),
}))

vi.mock('../api/client', () => ({
  getStock,
  getStockArtists,
}))

beforeEach(() => {
  vi.clearAllMocks()
  getStock.mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] })
  getStockArtists.mockResolvedValue([])
  localStorage.clear()
})

describe('refetch on stock sync progress', () => {
  it('reloads stock items and artists each time syncGeneration increments', async () => {
    const { rerender } = render(<StockBrowser syncGeneration={0} />)
    await waitFor(() => expect(getStock).toHaveBeenCalledTimes(1))
    expect(getStockArtists).toHaveBeenCalledTimes(1)

    rerender(<StockBrowser syncGeneration={1} />)
    await waitFor(() => expect(getStock).toHaveBeenCalledTimes(2))
    expect(getStockArtists).toHaveBeenCalledTimes(2)

    rerender(<StockBrowser syncGeneration={2} />)
    await waitFor(() => expect(getStock).toHaveBeenCalledTimes(3))
    expect(getStockArtists).toHaveBeenCalledTimes(3)
  })

  it('does not reload again when syncGeneration stays the same', async () => {
    const { rerender } = render(<StockBrowser syncGeneration={0} />)
    await waitFor(() => expect(getStock).toHaveBeenCalledTimes(1))

    rerender(<StockBrowser syncGeneration={1} />)
    await waitFor(() => expect(getStock).toHaveBeenCalledTimes(2))

    rerender(<StockBrowser syncGeneration={1} />)
    await waitFor(() => expect(getStock).toHaveBeenCalledTimes(2))
  })
})
