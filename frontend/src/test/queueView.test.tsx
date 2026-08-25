import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import QueueView from '../views/QueueView'
import type { QueueSummary, QueueCrawlerSummary } from '../api/types'

const { getQueueSummary, getQueueNext } = vi.hoisted(() => ({
  getQueueSummary: vi.fn(),
  getQueueNext: vi.fn(),
}))

vi.mock('../api/client', () => ({ getQueueSummary, getQueueNext }))

function crawler(over: Partial<QueueCrawlerSummary> = {}): QueueCrawlerSummary {
  return {
    crawler_id: 1,
    site_name: 'Amazon',
    requires_discogs_release: false,
    claimable_units: 10,
    held_units: 0,
    in_progress_units: 0,
    release_units: 7,
    stock_units: 3,
    oldest_wait_seconds: 7200,
    age_buckets: { under_1h: 2, under_24h: 5, over_24h: 3 },
    results_last_hour: 4,
    last_result_seconds_ago: 90,
    eta_seconds: 3600,
    ...over,
  }
}

function summary(over: Partial<QueueSummary> = {}): QueueSummary {
  return {
    totals: {
      claimable_rows: 10, claimable_release_rows: 7, claimable_stock_rows: 3,
      held_rows: 1, unactionable_rows: 0, in_progress_rows: 2, stranded_rows: 0,
      rows_done_last_hour: 20, eta_seconds: 1800,
      claimable_units: 10, held_units: 1, in_progress_units: 2,
    },
    crawlers: [crawler()],
    stranded_after_seconds: 1800,
    activity_window_seconds: 3600,
    pool_running: true,
    generated_at: '2026-08-25T00:00:00Z',
    ...over,
  }
}

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true })
  fireEvent(document, new Event('visibilitychange'))
}

beforeEach(() => {
  vi.clearAllMocks()
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
  getQueueSummary.mockResolvedValue(summary())
  getQueueNext.mockResolvedValue([])
})

describe('QueueView', () => {
  it('renders the stat tiles from the summary', async () => {
    render(<QueueView />)
    expect(await screen.findByText('Worker pool')).toBeInTheDocument()
    expect(screen.getByText('Running')).toBeInTheDocument()
    expect(screen.getByText('Stranded')).toBeInTheDocument()
  })

  it('labels the ring centre in rows and the segments in work units', async () => {
    render(<QueueView />)
    // 10 claimable + 1 held + 2 in progress rows, distinct from the unit counts
    // the segments carry.
    const donut = await screen.findByRole('img', { name: /work units by queue state/i })
    expect(donut).toHaveTextContent('13')
    expect(donut).toHaveTextContent('rows')
    expect(screen.getByText('Work units by crawler')).toBeInTheDocument()
  })

  it('prompts for a selection before a crawler is chosen', async () => {
    render(<QueueView />)
    expect(await screen.findByText(/Select a crawler above/)).toBeInTheDocument()
    expect(getQueueNext).not.toHaveBeenCalled()
  })

  it('loads the next-up list for the crawler that is clicked', async () => {
    getQueueNext.mockResolvedValue([
      { artist: 'Fugazi', title: 'Repeater', kind: 'release', waiting_seconds: 120, narrowed: false },
    ])
    render(<QueueView />)
    fireEvent.click(await screen.findByRole('button', { name: /Amazon/ }))

    await waitFor(() => expect(getQueueNext).toHaveBeenCalledWith(1))
    expect(await screen.findByText(/Fugazi/)).toBeInTheDocument()
    expect(screen.getByText('Age & composition')).toBeInTheDocument()
    expect(screen.getByText('Throughput & ETA')).toBeInTheDocument()
    expect(screen.getByText('Next up')).toBeInTheDocument()
  })

  it('says so when a selected crawler has nothing claimable', async () => {
    render(<QueueView />)
    fireEvent.click(await screen.findByRole('button', { name: /Amazon/ }))
    expect(await screen.findByText(/Nothing claimable for this crawler/)).toBeInTheDocument()
  })

  it('notes when a crawler cannot take stock-item targets', async () => {
    getQueueSummary.mockResolvedValue(summary({
      crawlers: [crawler({ requires_discogs_release: true, stock_units: 0 })],
    }))
    render(<QueueView />)
    fireEvent.click(await screen.findByRole('button', { name: /Amazon/ }))
    expect(await screen.findByText(/Requires a Discogs release/)).toBeInTheDocument()
  })

  it('filters the crawler list to the clicked ring state', async () => {
    getQueueSummary.mockResolvedValue(summary({
      crawlers: [
        crawler({ crawler_id: 1, site_name: 'Amazon', held_units: 0 }),
        crawler({ crawler_id: 2, site_name: 'eBay', held_units: 4 }),
      ],
    }))
    render(<QueueView />)
    await screen.findByRole('button', { name: /Amazon/ })

    fireEvent.click(screen.getByRole('button', { name: /^Held/ }))
    await waitFor(() => expect(screen.queryByRole('button', { name: /Amazon/ })).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: /eBay/ })).toBeInTheDocument()
  })

  it('filters the crawler list on the In progress segment too', async () => {
    getQueueSummary.mockResolvedValue(summary({
      crawlers: [
        crawler({ crawler_id: 1, site_name: 'Amazon', in_progress_units: 0 }),
        crawler({ crawler_id: 2, site_name: 'eBay', in_progress_units: 3 }),
      ],
    }))
    render(<QueueView />)
    await screen.findByRole('button', { name: /Amazon/ })

    fireEvent.click(screen.getByRole('button', { name: /^In progress/ }))
    await waitFor(() => expect(screen.queryByRole('button', { name: /Amazon/ })).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: /eBay/ })).toBeInTheDocument()
  })

  it('shows the filtered state\'s own units, not the pending ones', async () => {
    getQueueSummary.mockResolvedValue(summary({
      crawlers: [crawler({
        crawler_id: 2, site_name: 'eBay',
        claimable_units: 0, held_units: 0, in_progress_units: 3,
      })],
    }))
    render(<QueueView />)
    // Unfiltered it has no claimable work, so its row reads 0.
    expect(await screen.findByRole('button', { name: /eBay/ })).toHaveTextContent('0')

    // Filtered by In progress it must not still render as a bare "0" -- that
    // is a crawler matching the filter while appearing to have no work.
    fireEvent.click(screen.getByRole('button', { name: /^In progress/ }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /eBay/ })).toHaveTextContent('3'))
  })

  it('drops a selected crawler from the detail panels when a filter hides it', async () => {
    getQueueSummary.mockResolvedValue(summary({
      crawlers: [
        crawler({ crawler_id: 1, site_name: 'Amazon', held_units: 0 }),
        crawler({ crawler_id: 2, site_name: 'eBay', held_units: 4 }),
      ],
    }))
    render(<QueueView />)
    fireEvent.click(await screen.findByRole('button', { name: /Amazon/ }))
    expect(await screen.findByText('Age & composition')).toBeInTheDocument()

    // Amazon has no held work, so filtering to Held hides it from the list.
    // The panels below must not keep showing a crawler the list says isn't there.
    fireEvent.click(screen.getByRole('button', { name: /^Held/ }))
    await waitFor(() =>
      expect(screen.queryByText('Age & composition')).not.toBeInTheDocument())
    expect(screen.getByText(/Select a crawler above/)).toBeInTheDocument()
  })

  it('stops polling next-up for a crawler a filter has hidden', async () => {
    getQueueSummary.mockResolvedValue(summary({
      crawlers: [
        crawler({ crawler_id: 1, site_name: 'Amazon', held_units: 0 }),
        crawler({ crawler_id: 2, site_name: 'eBay', held_units: 4 }),
      ],
    }))
    render(<QueueView />)
    fireEvent.click(await screen.findByRole('button', { name: /Amazon/ }))
    await waitFor(() => expect(getQueueNext).toHaveBeenCalledWith(1))

    getQueueNext.mockClear()
    fireEvent.click(screen.getByRole('button', { name: /^Held/ }))
    // A hidden crawler must not keep costing a request on every poll.
    await waitFor(() =>
      expect(screen.queryByText('Age & composition')).not.toBeInTheDocument())
    expect(getQueueNext).not.toHaveBeenCalled()
  })

  it('restores the selection when the filter is cleared', async () => {
    getQueueSummary.mockResolvedValue(summary({
      crawlers: [
        crawler({ crawler_id: 1, site_name: 'Amazon', held_units: 0 }),
        crawler({ crawler_id: 2, site_name: 'eBay', held_units: 4 }),
      ],
    }))
    render(<QueueView />)
    fireEvent.click(await screen.findByRole('button', { name: /Amazon/ }))
    fireEvent.click(screen.getByRole('button', { name: /^Held/ }))
    await waitFor(() =>
      expect(screen.queryByText('Age & composition')).not.toBeInTheDocument())

    // Hiding is a view change, not a destruction of the selection.
    fireEvent.click(screen.getByRole('button', { name: /^Held/ }))
    expect(await screen.findByText('Age & composition')).toBeInTheDocument()
  })

  it('counts unactionable rows in the ring centre so it cannot contradict the tile', async () => {
    getQueueSummary.mockResolvedValue(summary({
      totals: {
        ...summary().totals,
        claimable_rows: 0, held_rows: 0, in_progress_rows: 0, unactionable_rows: 4,
        claimable_units: 0, held_units: 0, in_progress_units: 0,
      },
    }))
    render(<QueueView />)
    const donut = await screen.findByRole('img', { name: /work units by queue state/i })
    expect(donut).toHaveTextContent('4')
  })

  it('renders no composition bar for a crawler with nothing pending', async () => {
    getQueueSummary.mockResolvedValue(summary({
      crawlers: [crawler({
        claimable_units: 0, held_units: 0, in_progress_units: 2,
        release_units: 0, stock_units: 0,
      })],
    }))
    const { container } = render(<QueueView />)
    fireEvent.click(await screen.findByRole('button', { name: /Amazon/ }))
    await screen.findByText('Age & composition')

    // A 0% release segment used to leave the stock segment filling the whole
    // track, showing a full stock bar beside "0 release, 0 stock item".
    expect(container.querySelector('span.rounded-l-sm')).toBeNull()
  })

  it('does not stack requests when the tab is hidden and shown mid-request', async () => {
    let release: (v: unknown) => void = () => {}
    getQueueSummary.mockReturnValueOnce(new Promise((r) => { release = r }))
    render(<QueueView />)
    expect(getQueueSummary).toHaveBeenCalledTimes(1)

    // Stopping cannot recall the request already in flight, so restarting must
    // defer to it rather than issuing a second alongside. Alt-tabbing during a
    // slow query would otherwise stack repeatable-read transactions on the
    // pool the crawl workers claim through.
    setVisibility('hidden')
    setVisibility('visible')
    setVisibility('hidden')
    setVisibility('visible')
    expect(getQueueSummary).toHaveBeenCalledTimes(1)

    release(summary({ pool_running: true }))
    expect(await screen.findByText('Running')).toBeInTheDocument()
  })

  it('keeps polling after a hide/show that landed mid-request', async () => {
    vi.useFakeTimers()
    try {
      let release: (v: unknown) => void = () => {}
      getQueueSummary.mockReturnValueOnce(new Promise((r) => { release = r }))
      render(<QueueView />)
      setVisibility('hidden')
      setVisibility('visible')

      // The superseded request must hand the loop back, not leave it dead.
      release(summary())
      await vi.advanceTimersByTimeAsync(11_000)
      expect(getQueueSummary.mock.calls.length).toBeGreaterThan(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('never runs two summary requests at once', async () => {
    vi.useFakeTimers()
    try {
      getQueueSummary.mockReturnValue(new Promise(() => {}))
      render(<QueueView />)
      expect(getQueueSummary).toHaveBeenCalledTimes(1)

      // Three polling intervals' worth of time with the first request still
      // pending. setInterval would have stacked three more repeatable-read
      // transactions onto the pool the crawl workers claim through.
      await vi.advanceTimersByTimeAsync(30_000)
      expect(getQueueSummary).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('names the ETA tile claimable, since it ignores held and in-progress work', async () => {
    getQueueSummary.mockResolvedValue(summary({
      totals: {
        ...summary().totals,
        claimable_rows: 0, held_rows: 5, in_progress_rows: 2,
        rows_done_last_hour: 20, eta_seconds: 0,
      },
    }))
    render(<QueueView />)
    // "Queue ETA: 0s" beside five held rows would be a plain lie about the queue.
    expect(await screen.findByText('Claimable ETA')).toBeInTheDocument()
    expect(screen.queryByText('Queue ETA')).not.toBeInTheDocument()
  })

  it('names both causes of an unactionable row', async () => {
    render(<QueueView />)
    // Blaming only the crawler setting sends an operator to the wrong place
    // when the real cause is a stock item no enabled store lists any more.
    expect(await screen.findByText('no crawler, or no live source')).toBeInTheDocument()
  })

  it('says the worker pool tile is machine-local', async () => {
    render(<QueueView />)
    expect(await screen.findByText('this machine only')).toBeInTheDocument()
  })

  it('names the activity metric after what it measures', async () => {
    render(<QueueView />)
    fireEvent.click(await screen.findByRole('button', { name: /Amazon/ }))
    expect(await screen.findByText(/Listing rows touched/)).toBeInTheDocument()
    expect(screen.getByText(/not a count of searches/)).toBeInTheDocument()
  })

  it('marks the snapshot stale when a later poll fails', async () => {
    render(<QueueView />)
    await screen.findByText('Worker pool')

    getQueueSummary.mockRejectedValue(new Error('backend gone'))
    // Hiding then re-showing the tab is what actually re-triggers a load; a
    // bare visibilitychange while already visible is a no-op by design.
    setVisibility('hidden')
    setVisibility('visible')
    // Still showing the last good numbers, but no longer claiming they are live.
    expect(await screen.findByText(/Showing a stale snapshot/)).toBeInTheDocument()
    expect(screen.getByText('Worker pool')).toBeInTheDocument()
  })

  it('reports a failed next-up fetch instead of calling it empty', async () => {
    getQueueNext.mockRejectedValue(new Error('next blew up'))
    render(<QueueView />)
    fireEvent.click(await screen.findByRole('button', { name: /Amazon/ }))

    expect(await screen.findByText('next blew up')).toBeInTheDocument()
    expect(screen.queryByText(/Nothing claimable for this crawler/)).not.toBeInTheDocument()
  })

  it('reports a failure to load rather than rendering an empty frame', async () => {
    getQueueSummary.mockRejectedValue(new Error('nope'))
    render(<QueueView />)
    expect(await screen.findByText('nope')).toBeInTheDocument()
  })
})
