import type { ComponentProps } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import Settings from '../views/Settings'
import type { Crawler, Invite } from '../api/types'

const { getSettings, saveSettings, setCrawlerEnabled, listInvites, createInvite } = vi.hoisted(() => ({
  getSettings: vi.fn().mockResolvedValue({
    crawl_delay_seconds: 30,
    consecutive_failure_limit: 10,
    crawl_schedule: '',
    crawl_schedule_mode: 'missing',
    ebay_app_id: '',
    ebay_cert_id: '',
    stock_schedule: '',
  }),
  saveSettings: vi.fn().mockResolvedValue(undefined),
  setCrawlerEnabled: vi.fn().mockResolvedValue({ ok: true, discarded: 0 }),
  listInvites: vi.fn().mockResolvedValue([]),
  createInvite: vi.fn().mockResolvedValue({ code: 'NEWCODE123' }),
}))

vi.mock('../api/client', () => ({
  getSettings,
  saveSettings,
  setCrawlerEnabled,
  listInvites,
  createInvite,
}))

const CRAWLERS: Crawler[] = [
  { id: 1, site_name: 'Amazon', module_path: '', crawler_type: 'release', enabled: true, last_run: null, base_url: null },
  { id: 2, site_name: 'Disabled Site', module_path: '', crawler_type: 'release', enabled: false, last_run: null, base_url: null },
  { id: 3, site_name: 'Epitaph', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: null },
]

const INVITES: Invite[] = [
  { code: 'ABC123', note: 'for bob', created_by_username: 'admin', created_at: '2026-08-01T00:00:00', redeemed_by_username: null, redeemed_at: null },
]

const CATALOG_CRAWLERS_WITH_DISABLED: Crawler[] = [
  ...CRAWLERS,
  { id: 4, site_name: 'Disabled Catalog', module_path: '', crawler_type: 'catalog', enabled: false, last_run: null, base_url: null },
]

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.useRealTimers()
})

// The auto-save debounce is a real 800ms timer, so every test that waits it
// out calls vi.useFakeTimers() and advances the clock instead of sleeping:
// deterministic (a stalled CI worker fires a real timer late, which is what
// made these flake) and instant. waitFor is unusable once the clock is faked
// — its polling runs on that clock — so waits are act flushes instead.
const settle = () => act(async () => {})
const advanceBy = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })

function renderSettings(overrides: Partial<ComponentProps<typeof Settings>> = {}) {
  return render(
    <Settings
      crawlers={[]}
      onCrawlersChange={() => {}}
      onRefreshPrices={() => {}}
      onRefreshStock={() => {}}
      isAdmin
      hiddenCrawlerIds={[]}
      onToggleCrawlerView={() => {}}
      stockSyncBusy={false}
      stockSyncCrawlerId={null}
      onRefreshStoreCrawler={() => {}}
      {...overrides}
    />
  )
}

describe('Settings', () => {
  it('renders no page heading and no Save button', async () => {
    renderSettings()
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(screen.queryByText('Settings')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
  })

  it('auto-saves a settings field after editing, with no Save button', async () => {
    vi.useFakeTimers()
    renderSettings()
    await settle()
    expect(getSettings).toHaveBeenCalled()
    const input = screen.getByLabelText('eBay Client ID')
    fireEvent.change(input, { target: { value: 'new-app-id' } })
    await advanceBy(800)
    expect(saveSettings).toHaveBeenCalledWith(expect.objectContaining({ ebay_app_id: 'new-app-id' }))
  })

  it('does not save immediately on edit — only after the debounce settles', async () => {
    vi.useFakeTimers()
    renderSettings()
    await settle()
    fireEvent.change(screen.getByLabelText('eBay Client ID'), { target: { value: 'new-app-id' } })
    expect(saveSettings).not.toHaveBeenCalled()
    await advanceBy(799)
    expect(saveSettings).not.toHaveBeenCalled()
    await advanceBy(1)
    expect(saveSettings).toHaveBeenCalled()
  })

  it('does not auto-save on initial load when nothing was edited', async () => {
    vi.useFakeTimers()
    renderSettings()
    await settle()
    expect(getSettings).toHaveBeenCalled()
    await advanceBy(1200)
    expect(saveSettings).not.toHaveBeenCalled()
  })

  it('shows a clean error message (not raw JSON) when an auto-save fails', async () => {
    vi.useFakeTimers()
    saveSettings.mockRejectedValueOnce(new Error(JSON.stringify({ detail: 'Invalid cron expression' })))
    renderSettings()
    await settle()
    fireEvent.change(screen.getByLabelText('eBay Client ID'), { target: { value: 'new-app-id' } })
    await advanceBy(800)
    expect(screen.getByText('Invalid cron expression')).toBeInTheDocument()
    expect(screen.queryByText(/"detail"/)).not.toBeInTheDocument()
  })

  it('does not render the removed screenshot-interval or shuffle rows', async () => {
    renderSettings()
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(screen.queryByText('Screenshot interval')).not.toBeInTheDocument()
    expect(screen.queryByText('Shuffle')).not.toBeInTheDocument()
  })

  it('does not render Recommendations Management, moved to the Account view', async () => {
    renderSettings()
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(screen.queryByText('Recommendations Management')).not.toBeInTheDocument()
  })

  it('shows both View and Crawl columns to an admin, for every crawler regardless of enabled state', async () => {
    renderSettings({ crawlers: CRAWLERS })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(screen.getByText('Amazon')).toBeInTheDocument()
    expect(screen.getByText('Disabled Site')).toBeInTheDocument()
    expect(screen.getAllByText('Visible').length).toBe(3)
    expect(screen.getAllByText('Enabled').length).toBe(2)
    expect(screen.getAllByText('Disabled').length).toBe(1)
  })

  it('marks a crawler in hiddenCrawlerIds as Hidden in the View column', async () => {
    renderSettings({ crawlers: CRAWLERS, hiddenCrawlerIds: [1] })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const amazonRow = screen.getByText('Amazon').closest('tr') as HTMLElement
    expect(amazonRow.textContent).toContain('Hidden')
  })

  it('calls onToggleCrawlerView when a View button is clicked', async () => {
    const onToggleCrawlerView = vi.fn()
    renderSettings({ crawlers: CRAWLERS, onToggleCrawlerView })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const amazonRow = screen.getByText('Amazon').closest('tr') as HTMLElement
    fireEvent.click(screen.getAllByText('Visible').find((el) => amazonRow.contains(el))!)
    expect(onToggleCrawlerView).toHaveBeenCalledWith(1)
  })

  it('hides admin-only controls and the Crawl column for a non-admin, and only lists enabled crawlers', async () => {
    renderSettings({ crawlers: CRAWLERS, isAdmin: false })
    expect(getSettings).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
    expect(screen.getByText('Amazon')).toBeInTheDocument()
    expect(screen.queryByText('Disabled Site')).not.toBeInTheDocument()
    expect(screen.queryByText('Enabled')).not.toBeInTheDocument()
    expect(screen.getByText('Store Sources')).toBeInTheDocument()
    expect(screen.getByText('Store Catalog Sources')).toBeInTheDocument()
  })

  it('still shows View toggles to a non-admin', async () => {
    const onToggleCrawlerView = vi.fn()
    renderSettings({ crawlers: CRAWLERS, isAdmin: false, onToggleCrawlerView })
    const amazonRow = screen.getByText('Amazon').closest('tr') as HTMLElement
    fireEvent.click(screen.getAllByText('Visible').find((el) => amazonRow.contains(el))!)
    expect(onToggleCrawlerView).toHaveBeenCalledWith(1)
  })

  it('shows a per-row Refresh button only for catalog crawlers, and only to an admin', async () => {
    renderSettings({ crawlers: CRAWLERS })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const amazonRow = screen.getByText('Amazon').closest('tr') as HTMLElement // release crawler
    const epitaphRow = screen.getByText('Epitaph').closest('tr') as HTMLElement // catalog crawler
    expect(within(amazonRow).queryByTitle(/Refresh .* catalog now/)).not.toBeInTheDocument()
    expect(within(epitaphRow).getByTitle('Refresh Epitaph catalog now')).toBeInTheDocument()
  })

  it('does not show the per-row Refresh button to a non-admin', async () => {
    renderSettings({ crawlers: CRAWLERS, isAdmin: false })
    const epitaphRow = screen.getByText('Epitaph').closest('tr') as HTMLElement
    expect(within(epitaphRow).queryByTitle('Refresh Epitaph catalog now')).not.toBeInTheDocument()
  })

  it('disables the per-row Refresh button for a disabled catalog crawler', async () => {
    renderSettings({ crawlers: CATALOG_CRAWLERS_WITH_DISABLED })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const disabledRow = screen.getByText('Disabled Catalog').closest('tr') as HTMLElement
    expect(within(disabledRow).getByTitle('Refresh Disabled Catalog catalog now')).toBeDisabled()
  })

  it('disables every per-row Refresh button while a stock sync is running', async () => {
    renderSettings({ crawlers: CATALOG_CRAWLERS_WITH_DISABLED, stockSyncBusy: true })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(screen.getByTitle('Refresh Epitaph catalog now')).toBeDisabled()
  })

  it('calls onRefreshStoreCrawler with that crawler\'s id when its Refresh button is clicked', async () => {
    const onRefreshStoreCrawler = vi.fn()
    renderSettings({ crawlers: CRAWLERS, onRefreshStoreCrawler })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    fireEvent.click(screen.getByTitle('Refresh Epitaph catalog now'))
    expect(onRefreshStoreCrawler).toHaveBeenCalledWith(3)
  })

  it('disables the bulk Store Management Refresh button while a stock sync is running', async () => {
    renderSettings({ crawlers: CRAWLERS, stockSyncBusy: true })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const description = await screen.findByText('Scan all enabled catalog crawlers immediately.')
    const row = description.closest('tr') as HTMLElement
    expect(within(row).getByText('Refresh')).toBeDisabled()
  })

  it('buckets a catalog_browser crawler into the Store Catalog Sources table, not the release table', () => {
    const crawlers: Crawler[] = [
      ...CRAWLERS,
      { id: 4, site_name: 'Angry Young and Poor', module_path: '', crawler_type: 'catalog_browser', enabled: true, last_run: null, base_url: null },
    ]
    renderSettings({ crawlers, isAdmin: false })
    const tables = screen.getAllByRole('table')
    expect(within(tables[0]).queryByText('Angry Young and Poor')).not.toBeInTheDocument()
    expect(within(tables[1]).getByText('Angry Young and Poor')).toBeInTheDocument()
  })

  it('shows the discarded job count on the row that was just disabled', async () => {
    setCrawlerEnabled.mockResolvedValueOnce({ ok: true, discarded: 42 })
    renderSettings({ crawlers: CRAWLERS })
    await settle()

    const row = screen.getByText('Amazon').closest('tr') as HTMLElement
    fireEvent.click(within(row).getByText('Enabled'))
    await waitFor(() => expect(within(row).getByText('42 queued jobs discarded')).toBeInTheDocument())

    const otherRow = screen.getByText('Disabled Site').closest('tr') as HTMLElement
    expect(within(otherRow).queryByText(/queued jobs? discarded/)).not.toBeInTheDocument()
  })

  it('moves the notice when a second crawler is toggled', async () => {
    setCrawlerEnabled
      .mockResolvedValueOnce({ ok: true, discarded: 42 })
      .mockResolvedValueOnce({ ok: true, discarded: 7 })
    renderSettings({ crawlers: CRAWLERS })
    await settle()

    fireEvent.click(within(screen.getByText('Amazon').closest('tr') as HTMLElement).getByText('Enabled'))
    await waitFor(() => expect(screen.getByText('42 queued jobs discarded')).toBeInTheDocument())

    fireEvent.click(within(screen.getByText('Epitaph').closest('tr') as HTMLElement).getByText('Enabled'))
    await waitFor(() => expect(screen.getByText('7 queued jobs discarded')).toBeInTheDocument())
    expect(screen.queryByText('42 queued jobs discarded')).not.toBeInTheDocument()
  })

  it('surfaces a rejected toggle as an error and leaves the row showing its old state', async () => {
    setCrawlerEnabled.mockRejectedValueOnce(
      new Error(JSON.stringify({ detail: 'Admin access required' }))
    )
    const onCrawlersChange = vi.fn()
    renderSettings({ crawlers: CRAWLERS, onCrawlersChange })
    await settle()

    const row = screen.getByText('Amazon').closest('tr') as HTMLElement
    fireEvent.click(within(row).getByText('Enabled'))
    await waitFor(() => expect(screen.getByText('Admin access required')).toBeInTheDocument())

    expect(onCrawlersChange).not.toHaveBeenCalled()
    expect(within(row).getByText('Enabled')).toBeInTheDocument()
    expect(within(row).queryByText(/queued jobs? discarded/)).not.toBeInTheDocument()
  })

  // FastAPI's own 422s carry detail as an array of objects, not a string.
  // Returning that unchanged put a non-string into settingsSaveError, which
  // React throws on rendering — so the helper must fall back to the raw body.
  it('does not crash when a rejection carries a non-string detail', async () => {
    const body = JSON.stringify({ detail: [{ loc: ['body', 'enabled'], msg: 'field required' }] })
    setCrawlerEnabled.mockRejectedValueOnce(new Error(body))
    renderSettings({ crawlers: CRAWLERS })
    await settle()

    const row = screen.getByText('Amazon').closest('tr') as HTMLElement
    fireEvent.click(within(row).getByText('Enabled'))
    await waitFor(() => expect(screen.getByText(body)).toBeInTheDocument())
  })

  it('falls back to a default message when a rejection carries no message at all', async () => {
    setCrawlerEnabled.mockRejectedValueOnce(null)
    renderSettings({ crawlers: CRAWLERS })
    await settle()

    const row = screen.getByText('Amazon').closest('tr') as HTMLElement
    fireEvent.click(within(row).getByText('Enabled'))
    await waitFor(() => expect(screen.getByText('Could not change this crawler')).toBeInTheDocument())
  })

  it('shows no notice when nothing was discarded', async () => {
    setCrawlerEnabled.mockResolvedValueOnce({ ok: true, discarded: 0 })
    renderSettings({ crawlers: CRAWLERS })
    await settle()

    fireEvent.click(within(screen.getByText('Amazon').closest('tr') as HTMLElement).getByText('Enabled'))
    await settle()
    expect(screen.queryByText(/queued jobs? discarded/)).not.toBeInTheDocument()
  })

  it('does not show the Invites section to a non-admin', async () => {
    renderSettings({ isAdmin: false })
    expect(listInvites).not.toHaveBeenCalled()
    expect(screen.queryByText('Invites')).not.toBeInTheDocument()
  })

  it('loads and displays invites for an admin', async () => {
    listInvites.mockResolvedValueOnce(INVITES)
    renderSettings()
    await waitFor(() => expect(listInvites).toHaveBeenCalled())
    expect(await screen.findByText('ABC123')).toBeInTheDocument()
    expect(screen.getByText('for bob')).toBeInTheDocument()
  })

  // The backend serializes Postgres TIMESTAMP columns without a trailing Z
  // (a naive datetime's .isoformat()), unlike the Z-suffixed fixtures used
  // elsewhere in this file -- `new Date()` on an offsetless string parses as
  // browser-local time, so this must render as if the string were UTC.
  it('renders an offsetless server timestamp as UTC, not browser-local time', async () => {
    listInvites.mockResolvedValueOnce([
      { code: 'TZTEST1', note: null, created_by_username: 'admin', created_at: '2026-08-01T00:00:00', redeemed_by_username: null, redeemed_at: null },
    ])
    renderSettings()
    await waitFor(() => expect(listInvites).toHaveBeenCalled())
    expect(await screen.findByText('TZTEST1')).toBeInTheDocument()
    const expected = new Date('2026-08-01T00:00:00Z').toLocaleString()
    expect(screen.getByText(expected)).toBeInTheDocument()
  })

  it('shows a placeholder when no invites have been minted', async () => {
    renderSettings()
    await waitFor(() => expect(listInvites).toHaveBeenCalled())
    expect(screen.getByText('No invites minted yet.')).toBeInTheDocument()
  })

  it('mints a new invite, clears the note, and shows the code with a Copy button', async () => {
    renderSettings()
    await waitFor(() => expect(listInvites).toHaveBeenCalled())
    const noteInput = screen.getByLabelText('Invite note')
    fireEvent.change(noteInput, { target: { value: 'for carol' } })
    listInvites.mockResolvedValueOnce([
      { code: 'NEWCODE123', note: 'for carol', created_by_username: 'admin', created_at: '2026-08-11T00:00:00', redeemed_by_username: null, redeemed_at: null },
    ])
    fireEvent.click(screen.getByText('Generate'))
    await waitFor(() => expect(createInvite).toHaveBeenCalledWith('for carol'))
    // The refetched invite list also contains the just-minted code, so
    // 'NEWCODE123' legitimately appears twice on screen (mint display +
    // table row) — scope to the mint display via its Copy button sibling
    // rather than a bare getByText, which would ambiguously match both.
    const copyButton = await screen.findByText('Copy')
    expect(copyButton.closest('p')).toHaveTextContent('NEWCODE123')
    expect((noteInput as HTMLInputElement).value).toBe('')
  })

  it('copies the minted code to the clipboard when Copy is clicked', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    createInvite.mockResolvedValueOnce({ code: 'COPYME1' })
    renderSettings()
    await waitFor(() => expect(listInvites).toHaveBeenCalled())
    fireEvent.click(screen.getByText('Generate'))
    await waitFor(() => expect(screen.getByText('COPYME1')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Copy'))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('COPYME1'))
    expect(await screen.findByText('Copied')).toBeInTheDocument()
  })

  it('shows an error and keeps the note field when minting fails', async () => {
    createInvite.mockRejectedValueOnce(new Error(JSON.stringify({ detail: 'Rate limited' })))
    renderSettings()
    await waitFor(() => expect(listInvites).toHaveBeenCalled())
    const noteInput = screen.getByLabelText('Invite note')
    fireEvent.change(noteInput, { target: { value: 'for dave' } })
    fireEvent.click(screen.getByText('Generate'))
    await waitFor(() => expect(screen.getByText('Rate limited')).toBeInTheDocument())
    expect((noteInput as HTMLInputElement).value).toBe('for dave')
  })

  it('keeps the minted code and does not claim minting failed when the refetch fails', async () => {
    createInvite.mockResolvedValueOnce({ code: 'MINTED1' })
    renderSettings()
    await waitFor(() => expect(listInvites).toHaveBeenCalled())
    listInvites.mockRejectedValueOnce(new Error('network down'))
    fireEvent.click(screen.getByText('Generate'))
    expect(await screen.findByText('MINTED1')).toBeInTheDocument()
    expect(await screen.findByText('Invite created, but the list could not be refreshed.')).toBeInTheDocument()
    expect(screen.queryByText('Could not generate invite')).not.toBeInTheDocument()
  })

  it('does not show the empty-state message while the initial invites fetch is pending', async () => {
    let resolveInvites: (value: Invite[]) => void = () => {}
    listInvites.mockReturnValueOnce(new Promise<Invite[]>((resolve) => { resolveInvites = resolve }))
    renderSettings()
    await waitFor(() => expect(listInvites).toHaveBeenCalled())
    expect(screen.queryByText('No invites minted yet.')).not.toBeInTheDocument()
    resolveInvites([])
    expect(await screen.findByText('No invites minted yet.')).toBeInTheDocument()
  })

  it('does not let a slow initial fetch clobber a post-mint refetch that resolved first', async () => {
    let resolveInitial: (value: Invite[]) => void = () => {}
    listInvites.mockReturnValueOnce(new Promise<Invite[]>((resolve) => { resolveInitial = resolve }))
    renderSettings()
    await waitFor(() => expect(listInvites).toHaveBeenCalledTimes(1))

    listInvites.mockResolvedValueOnce([
      { code: 'FRESH1', note: null, created_by_username: 'admin', created_at: '2026-08-11T00:00:00', redeemed_by_username: null, redeemed_at: null },
    ])
    fireEvent.click(screen.getByText('Generate'))
    expect(await screen.findByText('FRESH1')).toBeInTheDocument()

    resolveInitial([
      { code: 'STALE1', note: null, created_by_username: 'admin', created_at: '2026-08-01T00:00:00', redeemed_by_username: null, redeemed_at: null },
    ])
    await settle()
    expect(screen.queryByText('STALE1')).not.toBeInTheDocument()
    expect(screen.getByText('FRESH1')).toBeInTheDocument()
  })

  it('shows an error when the clipboard is unavailable', async () => {
    const clipboard = navigator.clipboard
    Object.assign(navigator, { clipboard: undefined })
    createInvite.mockResolvedValueOnce({ code: 'NOCLIP1' })
    renderSettings()
    await waitFor(() => expect(listInvites).toHaveBeenCalled())
    fireEvent.click(screen.getByText('Generate'))
    await waitFor(() => expect(screen.getByText('NOCLIP1')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Copy'))
    expect(await screen.findByText(/Could not copy to the clipboard/)).toBeInTheDocument()
    expect(screen.queryByText('Copied')).not.toBeInTheDocument()
    Object.assign(navigator, { clipboard })
  })

  it('disables Generate while a mint is in flight, and re-enables once it settles', async () => {
    let resolveCreate: (value: { code: string }) => void = () => {}
    createInvite.mockReturnValueOnce(new Promise<{ code: string }>((resolve) => { resolveCreate = resolve }))
    renderSettings()
    await waitFor(() => expect(listInvites).toHaveBeenCalled())
    const generateButton = screen.getByRole('button', { name: 'Generate' })
    fireEvent.click(generateButton)
    await waitFor(() => expect(createInvite).toHaveBeenCalled())
    expect(generateButton).toBeDisabled()
    expect(screen.getByText('Generating…')).toBeInTheDocument()

    resolveCreate({ code: 'INFLIGHT1' })
    await waitFor(() => expect(generateButton).not.toBeDisabled())
    expect(screen.getByText('Generate')).toBeInTheDocument()
  })
})
