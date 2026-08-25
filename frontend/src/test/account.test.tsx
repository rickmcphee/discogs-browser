import type { ComponentProps } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
import Account from '../views/Account'
import type { Invite } from '../api/types'

const { uploadAvatar, deleteAvatar, logout, getUserSettings, saveUserSettings, postPlexMatchStart, listInvites, createInvite } = vi.hoisted(() => ({
  uploadAvatar: vi.fn().mockResolvedValue(undefined),
  deleteAvatar: vi.fn().mockResolvedValue(undefined),
  logout: vi.fn().mockResolvedValue(undefined),
  getUserSettings: vi.fn().mockResolvedValue({ anthropic_api_key: '', recommendation_item_limit: 300, plex_base_url: '', plex_token: '', plex_match_threshold: 90 }),
  saveUserSettings: vi.fn().mockResolvedValue(undefined),
  postPlexMatchStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  listInvites: vi.fn().mockResolvedValue([]),
  createInvite: vi.fn().mockResolvedValue({ code: 'NEWCODE123' }),
}))

vi.mock('../api/client', () => ({
  uploadAvatar,
  deleteAvatar,
  logout,
  getUserSettings,
  saveUserSettings,
  postPlexMatchStart,
  listInvites,
  createInvite,
  avatarUrl: (v: number) => `/api/auth/avatar?v=${v}`,
}))

const INVITES: Invite[] = [
  { code: 'ABC123', note: 'for bob', created_by_username: 'admin', created_at: '2026-08-01T00:00:00', redeemed_by_username: null, redeemed_at: null },
]

function renderAccount(overrides: Partial<ComponentProps<typeof Account>> = {}) {
  return render(
    <Account avatarVersion={0} onAvatarChange={() => {}} isAdmin {...overrides} />
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
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

describe('Account', () => {
  it('shows "Change photo" but not "Remove photo" when there is no avatar', () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    expect(screen.getByText('Change photo')).toBeInTheDocument()
    expect(screen.queryByText('Remove photo')).not.toBeInTheDocument()
  })

  it('shows "Remove photo" when an avatar exists, and removing it calls deleteAvatar', async () => {
    const onAvatarChange = vi.fn()
    render(<Account avatarVersion={123} onAvatarChange={onAvatarChange} />)
    fireEvent.click(screen.getByText('Remove photo'))
    await waitFor(() => expect(deleteAvatar).toHaveBeenCalled())
    expect(onAvatarChange).toHaveBeenCalledWith(0)
  })

  it('uploads the selected file and reports a new version', async () => {
    const onAvatarChange = vi.fn()
    render(<Account avatarVersion={0} onAvatarChange={onAvatarChange} />)
    const file = new File(['x'], 'photo.png', { type: 'image/png' })
    const input = screen.getByTestId('avatar-file-input') as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })
    await waitFor(() => expect(uploadAvatar).toHaveBeenCalledWith(file))
    await waitFor(() => expect(onAvatarChange).toHaveBeenCalled())
  })

  it('shows an inline error when the upload fails', async () => {
    uploadAvatar.mockRejectedValueOnce(new Error('File too large'))
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    const file = new File(['x'], 'photo.png', { type: 'image/png' })
    const input = screen.getByTestId('avatar-file-input') as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })
    await waitFor(() => expect(screen.getByText('File too large')).toBeInTheDocument())
  })

  it('logs out when "Log out" is clicked', async () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    fireEvent.click(screen.getByText('Log out'))
    await waitFor(() => expect(logout).toHaveBeenCalled())
  })

  it('auto-saves the anthropic API key field after editing, with no Save button', async () => {
    vi.useFakeTimers()
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await settle()
    expect(getUserSettings).toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Save Recommendations settings' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save Plex settings' })).not.toBeInTheDocument()
    const input = screen.getByLabelText('Anthropic API key')
    fireEvent.change(input, { target: { value: 'sk-ant-new-key' } })
    await advanceBy(800)
    expect(saveUserSettings).toHaveBeenCalledWith(
      expect.objectContaining({ anthropic_api_key: 'sk-ant-new-key' })
    )
  })

  it('still auto-saves the first real edit when fetched settings equal the field defaults', async () => {
    vi.useFakeTimers()
    getUserSettings.mockResolvedValueOnce({
      anthropic_api_key: '',
      recommendation_item_limit: 300,
      plex_base_url: '',
      plex_token: '',
      plex_match_threshold: 90,
    })
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await settle()
    expect(getUserSettings).toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('Anthropic API key'), { target: { value: 'sk-ant-new-key' } })
    await advanceBy(800)
    expect(saveUserSettings).toHaveBeenCalledWith(
      expect.objectContaining({ anthropic_api_key: 'sk-ant-new-key' })
    )
  })

  it('does not save immediately on edit — only after the debounce settles', async () => {
    vi.useFakeTimers()
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await settle()
    fireEvent.change(screen.getByLabelText('Anthropic API key'), { target: { value: 'sk-ant-new-key' } })
    expect(saveUserSettings).not.toHaveBeenCalled()
    await advanceBy(799)
    expect(saveUserSettings).not.toHaveBeenCalled()
    await advanceBy(1)
    expect(saveUserSettings).toHaveBeenCalled()
  })

  it('does not auto-save on initial load when nothing was edited', async () => {
    vi.useFakeTimers()
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await settle()
    expect(getUserSettings).toHaveBeenCalled()
    await advanceBy(1200)
    expect(saveUserSettings).not.toHaveBeenCalled()
  })

  it('shows a clean error message (not raw JSON) when an auto-save fails', async () => {
    vi.useFakeTimers()
    saveUserSettings.mockRejectedValueOnce(new Error(JSON.stringify({ detail: 'Plex address not reachable' })))
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await settle()
    fireEvent.change(screen.getByLabelText('Plex server address'), { target: { value: 'http://bad-address' } })
    await advanceBy(800)
    expect(screen.getByText('Plex address not reachable')).toBeInTheDocument()
    expect(screen.queryByText(/"detail"/)).not.toBeInTheDocument()
  })

  it('disables Link Now when Plex is not configured', async () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: 'Link Now' })).toBeDisabled()
  })

  it('enables Link Now once Plex is configured and calls postPlexMatchStart when clicked', async () => {
    getUserSettings.mockResolvedValueOnce({
      anthropic_api_key: '', recommendation_item_limit: 300,
      plex_base_url: 'https://plex.local:32400', plex_token: 'tok', plex_match_threshold: 90,
    })
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    // The button renders with this name before settings load, so findByRole
    // matches the still-disabled render — wait for it to enable, don't assert.
    const button = await screen.findByRole('button', { name: 'Link Now' })
    await waitFor(() => expect(button).not.toBeDisabled())
    fireEvent.click(button)
    await waitFor(() => expect(postPlexMatchStart).toHaveBeenCalledTimes(1))
  })

  it('shows a clean error message (not raw JSON) when Link Now fails', async () => {
    postPlexMatchStart.mockRejectedValueOnce(new Error(JSON.stringify({ detail: 'Plex address not reachable' })))
    getUserSettings.mockResolvedValueOnce({
      anthropic_api_key: '', recommendation_item_limit: 300,
      plex_base_url: 'https://plex.local:32400', plex_token: 'tok', plex_match_threshold: 90,
    })
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    const button = await screen.findByRole('button', { name: 'Link Now' })
    await waitFor(() => expect(button).not.toBeDisabled())
    fireEvent.click(button)
    await waitFor(() => expect(screen.getByText('Plex address not reachable')).toBeInTheDocument())
    expect(screen.queryByText(/"detail"/)).not.toBeInTheDocument()
  })

  it('disables Export until a judgment has completed', async () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} hasJudgedItems={false} />)
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: 'Export' })).toBeDisabled()
  })

  it('enables Export once a judgment has completed and calls onExportRecommendations when clicked', async () => {
    const onExportRecommendations = vi.fn()
    render(
      <Account
        avatarVersion={0}
        onAvatarChange={() => {}}
        hasJudgedItems
        onExportRecommendations={onExportRecommendations}
      />
    )
    const button = await screen.findByRole('button', { name: 'Export' })
    expect(button).not.toBeDisabled()
    fireEvent.click(button)
    expect(onExportRecommendations).toHaveBeenCalledTimes(1)
  })

  it('shows Export for a non-admin', () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} isAdmin={false} />)
    expect(screen.getByRole('button', { name: 'Export' })).toBeInTheDocument()
  })

  it('shows Refresh, Export, and Clear in that order, and Refresh is always enabled', async () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} hasJudgedItems={false} />)
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())
    const buttons = screen.getAllByRole('button').filter((b) =>
      ['Refresh', 'Export', 'Clear'].includes(b.textContent ?? '')
    )
    expect(buttons.map((b) => b.textContent)).toEqual(['Refresh', 'Export', 'Clear'])
    expect(screen.getByRole('button', { name: 'Refresh' })).not.toBeDisabled()
  })

  it('calls onRefreshRecommendations when Refresh is clicked', async () => {
    const onRefreshRecommendations = vi.fn()
    render(
      <Account
        avatarVersion={0}
        onAvatarChange={() => {}}
        onRefreshRecommendations={onRefreshRecommendations}
      />
    )
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    expect(onRefreshRecommendations).toHaveBeenCalledTimes(1)
  })

  it('disables Clear until a judgment has completed, and calls onClearRecommendations when clicked', async () => {
    const onClearRecommendations = vi.fn()
    const { rerender } = render(
      <Account
        avatarVersion={0}
        onAvatarChange={() => {}}
        hasJudgedItems={false}
        onClearRecommendations={onClearRecommendations}
      />
    )
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: 'Clear' })).toBeDisabled()

    rerender(
      <Account
        avatarVersion={0}
        onAvatarChange={() => {}}
        hasJudgedItems
        onClearRecommendations={onClearRecommendations}
      />
    )
    const button = screen.getByRole('button', { name: 'Clear' })
    expect(button).not.toBeDisabled()
    fireEvent.click(button)
    expect(onClearRecommendations).toHaveBeenCalledTimes(1)
  })

  it('shows Refresh and Clear for a non-admin', () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} isAdmin={false} />)
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Clear' })).toBeInTheDocument()
  })

  it('does not show the role switch by default', () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  })

  it('shows the role switch for an admin, labeled "Admin" when not viewing as user', () => {
    render(
      <Account avatarVersion={0} onAvatarChange={() => {}} isAdmin viewingAsUser={false} />
    )
    const roleSwitch = screen.getByRole('switch', { name: 'Toggle admin/user view' })
    expect(roleSwitch).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByText('Admin')).toBeInTheDocument()
  })

  it('labels the role switch "User" and checks it when viewing as user', () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} isAdmin viewingAsUser />)
    const roleSwitch = screen.getByRole('switch', { name: 'Toggle admin/user view' })
    expect(roleSwitch).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByText('User')).toBeInTheDocument()
  })

  it('calls onToggleViewAsUser when the role switch is clicked', () => {
    const onToggleViewAsUser = vi.fn()
    render(
      <Account
        avatarVersion={0}
        onAvatarChange={() => {}}
        isAdmin
        onToggleViewAsUser={onToggleViewAsUser}
      />
    )
    fireEvent.click(screen.getByRole('switch', { name: 'Toggle admin/user view' }))
    expect(onToggleViewAsUser).toHaveBeenCalledTimes(1)
  })

  it('has no Account & Security section, but still shows Log out for a non-admin', () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    expect(screen.queryByText('Account & Security')).not.toBeInTheDocument()
    expect(screen.getByText('Log out')).toBeInTheDocument()
  })

  it('positions Log out to the right of the role switch for an admin', () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} isAdmin />)
    const roleSwitch = screen.getByRole('switch', { name: 'Toggle admin/user view' })
    const logoutButton = screen.getByText('Log out')
    // DOCUMENT_POSITION_FOLLOWING (4) means logoutButton comes after roleSwitch
    // in DOM order, which is left-to-right in their shared flex row.
    expect(roleSwitch.compareDocumentPosition(logoutButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('clears the stored view-as-user flag when logging out', async () => {
    localStorage.setItem('discogs-browser.viewAsUser', 'true')
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    fireEvent.click(screen.getByText('Log out'))
    await waitFor(() => expect(logout).toHaveBeenCalled())
    expect(localStorage.getItem('discogs-browser.viewAsUser')).toBeNull()
  })

  it('keeps the stored view-as-user flag when logout fails', async () => {
    logout.mockRejectedValueOnce(new Error('Network error'))
    localStorage.setItem('discogs-browser.viewAsUser', 'true')
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    fireEvent.click(screen.getByText('Log out'))
    await waitFor(() => expect(logout).toHaveBeenCalled())
    expect(localStorage.getItem('discogs-browser.viewAsUser')).toBe('true')
  })

  it('clears a stale save error as soon as the user edits a field again', async () => {
    vi.useFakeTimers()
    saveUserSettings.mockRejectedValueOnce(new Error(JSON.stringify({ detail: 'Plex address not reachable' })))
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await settle()
    fireEvent.change(screen.getByLabelText('Plex server address'), { target: { value: 'http://bad-address' } })
    await advanceBy(800)
    expect(screen.getByText('Plex address not reachable')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Plex server address'), { target: { value: 'http://still-typing' } })
    expect(screen.queryByText('Plex address not reachable')).not.toBeInTheDocument()
  })

  it('serializes overlapping saves so a slower earlier save cannot overwrite a later one', async () => {
    vi.useFakeTimers()
    let resolveFirstSave: () => void = () => {}
    let resolveSecondSave: () => void = () => {}
    const firstSave = new Promise<void>((resolve) => { resolveFirstSave = resolve })
    const secondSave = new Promise<void>((resolve) => { resolveSecondSave = resolve })
    saveUserSettings.mockImplementationOnce(() => firstSave)
    saveUserSettings.mockImplementationOnce(() => secondSave)

    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await settle()

    const input = screen.getByLabelText('Anthropic API key')
    fireEvent.change(input, { target: { value: 'first-edit' } })
    await advanceBy(800)
    expect(saveUserSettings).toHaveBeenCalledTimes(1)
    expect(saveUserSettings).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ anthropic_api_key: 'first-edit' })
    )

    // Trigger a second debounced save while the first request is still
    // in flight (its promise hasn't resolved yet), and let that second
    // debounce window fully elapse. If saves were fired concurrently
    // instead of serialized, saveUserSettings would already have been
    // called a second time by now.
    fireEvent.change(input, { target: { value: 'second-edit' } })
    await advanceBy(1200)
    expect(saveUserSettings).toHaveBeenCalledTimes(1)

    // Only once the first request settles should the second one's actual
    // network call fire.
    resolveFirstSave()
    await settle()
    expect(saveUserSettings).toHaveBeenCalledTimes(2)
    expect(saveUserSettings).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ anthropic_api_key: 'second-edit' })
    )

    resolveSecondSave()
  })

  it('suppresses a stale error from a save already superseded by a newer edit', async () => {
    vi.useFakeTimers()
    let rejectFirstSave: (err: Error) => void = () => {}
    let resolveSecondSave: () => void = () => {}
    const firstSave = new Promise<void>((_resolve, reject) => { rejectFirstSave = reject })
    const secondSave = new Promise<void>((resolve) => { resolveSecondSave = resolve })
    saveUserSettings.mockImplementationOnce(() => firstSave)
    saveUserSettings.mockImplementationOnce(() => secondSave)

    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await settle()

    const input = screen.getByLabelText('Anthropic API key')
    fireEvent.change(input, { target: { value: 'first-edit' } })
    await advanceBy(800)
    expect(saveUserSettings).toHaveBeenCalledTimes(1)

    // Trigger a second debounced save while the first request is still in
    // flight -- once its debounce fires it claims sequence #2 and queues
    // behind the first in the chain, without firing a request of its own.
    // That claim is what the first save's error checks itself against, so
    // it has to have happened before the rejection below.
    fireEvent.change(input, { target: { value: 'second-edit' } })
    await advanceBy(800)
    expect(saveUserSettings).toHaveBeenCalledTimes(1)

    // Reject the now-superseded first save and let its catch block and
    // save #2's handoff run to completion.
    rejectFirstSave(new Error('stale save failed'))
    await settle()

    // The first save's error must never reach the screen -- a newer save
    // (#2) has already superseded it.
    expect(screen.queryByText('stale save failed')).not.toBeInTheDocument()
    expect(saveUserSettings).toHaveBeenCalledTimes(2)

    resolveSecondSave()
    await settle()
    expect(screen.queryByText('stale save failed')).not.toBeInTheDocument()
  })

  it('renders Import between Export and Clear', async () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} hasJudgedItems={true} />)
    await screen.findByRole('button', { name: 'Export' })
    const labels = screen.getAllByRole('button')
      .map((b) => b.textContent)
      .filter((t) => t === 'Refresh' || t === 'Export' || t === 'Import' || t === 'Clear')
    expect(labels).toEqual(['Refresh', 'Export', 'Import', 'Clear'])
  })

  it('enables Import even when nothing has been judged yet', () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} hasJudgedItems={false} />)
    expect(screen.getByRole('button', { name: 'Import' })).toBeEnabled()
  })

  it('passes the selected file to onImportRecommendations and clears the input', async () => {
    const onImportRecommendations = vi.fn()
    render(
      <Account
        avatarVersion={0}
        onAvatarChange={() => {}}
        onImportRecommendations={onImportRecommendations}
      />,
    )
    const file = new File(['artist,title\n'], 'recommendations.csv', { type: 'text/csv' })
    const input = screen.getByTestId('recommendations-import-input') as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })
    await waitFor(() => expect(onImportRecommendations).toHaveBeenCalledWith(file))
    // Cleared so re-picking the same file fires change again.
    expect(input.value).toBe('')
  })

  it('does not show the Invites section to a non-admin', async () => {
    renderAccount({ isAdmin: false })
    expect(listInvites).not.toHaveBeenCalled()
    expect(screen.queryByText('Invites')).not.toBeInTheDocument()
  })

  it('does not show the Invites section while previewing as a user', async () => {
    renderAccount({ viewingAsUser: true })
    expect(listInvites).not.toHaveBeenCalled()
    expect(screen.queryByText('Invites')).not.toBeInTheDocument()
  })

  it('loads and displays invites for an admin', async () => {
    listInvites.mockResolvedValueOnce(INVITES)
    renderAccount()
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
    renderAccount()
    await waitFor(() => expect(listInvites).toHaveBeenCalled())
    expect(await screen.findByText('TZTEST1')).toBeInTheDocument()
    const expected = new Date('2026-08-01T00:00:00Z').toLocaleString()
    expect(screen.getByText(expected)).toBeInTheDocument()
  })

  it('shows a placeholder when no invites have been minted', async () => {
    renderAccount()
    await waitFor(() => expect(listInvites).toHaveBeenCalled())
    expect(screen.getByText('No invites minted yet.')).toBeInTheDocument()
  })

  it('mints a new invite, clears the note, and shows the code with a Copy button', async () => {
    renderAccount()
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
    renderAccount()
    await waitFor(() => expect(listInvites).toHaveBeenCalled())
    fireEvent.click(screen.getByText('Generate'))
    await waitFor(() => expect(screen.getByText('COPYME1')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Copy'))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('COPYME1'))
    expect(await screen.findByText('Copied')).toBeInTheDocument()
  })

  it('shows an error and keeps the note field when minting fails', async () => {
    createInvite.mockRejectedValueOnce(new Error(JSON.stringify({ detail: 'Rate limited' })))
    renderAccount()
    await waitFor(() => expect(listInvites).toHaveBeenCalled())
    const noteInput = screen.getByLabelText('Invite note')
    fireEvent.change(noteInput, { target: { value: 'for dave' } })
    fireEvent.click(screen.getByText('Generate'))
    await waitFor(() => expect(screen.getByText('Rate limited')).toBeInTheDocument())
    expect((noteInput as HTMLInputElement).value).toBe('for dave')
  })

  it('keeps the minted code and does not claim minting failed when the refetch fails', async () => {
    createInvite.mockResolvedValueOnce({ code: 'MINTED1' })
    renderAccount()
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
    renderAccount()
    await waitFor(() => expect(listInvites).toHaveBeenCalled())
    expect(screen.queryByText('No invites minted yet.')).not.toBeInTheDocument()
    resolveInvites([])
    expect(await screen.findByText('No invites minted yet.')).toBeInTheDocument()
  })

  it('does not let a slow initial fetch clobber a post-mint refetch that resolved first', async () => {
    let resolveInitial: (value: Invite[]) => void = () => {}
    listInvites.mockReturnValueOnce(new Promise<Invite[]>((resolve) => { resolveInitial = resolve }))
    renderAccount()
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
    renderAccount()
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
    renderAccount()
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
