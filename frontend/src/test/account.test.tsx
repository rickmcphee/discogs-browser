import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import Account from '../views/Account'

const { uploadAvatar, deleteAvatar, logout, getUserSettings, saveUserSettings } = vi.hoisted(() => ({
  uploadAvatar: vi.fn().mockResolvedValue(undefined),
  deleteAvatar: vi.fn().mockResolvedValue(undefined),
  logout: vi.fn().mockResolvedValue(undefined),
  getUserSettings: vi.fn().mockResolvedValue({ anthropic_api_key: '', recommendation_item_limit: 300, plex_base_url: '', plex_token: '', plex_match_threshold: 90 }),
  saveUserSettings: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('../api/client', () => ({
  uploadAvatar,
  deleteAvatar,
  logout,
  getUserSettings,
  saveUserSettings,
  avatarUrl: (v: number) => `/api/auth/avatar?v=${v}`,
}))

beforeEach(() => {
  vi.clearAllMocks()
})

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
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: 'Save Recommendations settings' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save Plex settings' })).not.toBeInTheDocument()
    const input = screen.getByLabelText('Anthropic API key')
    fireEvent.change(input, { target: { value: 'sk-ant-new-key' } })
    await waitFor(
      () =>
        expect(saveUserSettings).toHaveBeenCalledWith(
          expect.objectContaining({ anthropic_api_key: 'sk-ant-new-key' })
        ),
      { timeout: 2000 }
    )
  })

  it('still auto-saves the first real edit when fetched settings equal the field defaults', async () => {
    getUserSettings.mockResolvedValueOnce({
      anthropic_api_key: '',
      recommendation_item_limit: 300,
      plex_base_url: '',
      plex_token: '',
      plex_match_threshold: 90,
    })
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())
    fireEvent.change(screen.getByLabelText('Anthropic API key'), { target: { value: 'sk-ant-new-key' } })
    await waitFor(
      () =>
        expect(saveUserSettings).toHaveBeenCalledWith(
          expect.objectContaining({ anthropic_api_key: 'sk-ant-new-key' })
        ),
      { timeout: 2000 }
    )
  })

  it('does not save immediately on edit — only after the debounce settles', async () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())
    fireEvent.change(screen.getByLabelText('Anthropic API key'), { target: { value: 'sk-ant-new-key' } })
    expect(saveUserSettings).not.toHaveBeenCalled()
    await waitFor(() => expect(saveUserSettings).toHaveBeenCalled(), { timeout: 2000 })
  })

  it('does not auto-save on initial load when nothing was edited', async () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 1200))
    expect(saveUserSettings).not.toHaveBeenCalled()
  })

  it('shows a clean error message (not raw JSON) when an auto-save fails', async () => {
    saveUserSettings.mockRejectedValueOnce(new Error(JSON.stringify({ detail: 'Plex address not reachable' })))
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())
    fireEvent.change(screen.getByLabelText('Plex server address'), { target: { value: 'http://bad-address' } })
    await waitFor(
      () => expect(screen.getByText('Plex address not reachable')).toBeInTheDocument(),
      { timeout: 2000 }
    )
    expect(screen.queryByText(/"detail"/)).not.toBeInTheDocument()
  })

  it('clears a stale save error as soon as the user edits a field again', async () => {
    saveUserSettings.mockRejectedValueOnce(new Error(JSON.stringify({ detail: 'Plex address not reachable' })))
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())
    fireEvent.change(screen.getByLabelText('Plex server address'), { target: { value: 'http://bad-address' } })
    await waitFor(
      () => expect(screen.getByText('Plex address not reachable')).toBeInTheDocument(),
      { timeout: 2000 }
    )
    fireEvent.change(screen.getByLabelText('Plex server address'), { target: { value: 'http://still-typing' } })
    expect(screen.queryByText('Plex address not reachable')).not.toBeInTheDocument()
  })

  it('serializes overlapping saves so a slower earlier save cannot overwrite a later one', async () => {
    let resolveFirstSave: () => void = () => {}
    let resolveSecondSave: () => void = () => {}
    const firstSave = new Promise<void>((resolve) => { resolveFirstSave = resolve })
    const secondSave = new Promise<void>((resolve) => { resolveSecondSave = resolve })
    saveUserSettings.mockImplementationOnce(() => firstSave)
    saveUserSettings.mockImplementationOnce(() => secondSave)

    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())

    const input = screen.getByLabelText('Anthropic API key')
    fireEvent.change(input, { target: { value: 'first-edit' } })
    await waitFor(() => expect(saveUserSettings).toHaveBeenCalledTimes(1), { timeout: 2000 })
    expect(saveUserSettings).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ anthropic_api_key: 'first-edit' })
    )

    // Trigger a second debounced save while the first request is still
    // in flight (its promise hasn't resolved yet).
    fireEvent.change(input, { target: { value: 'second-edit' } })

    // Let the second debounce window (800ms) fully elapse. If saves were
    // fired concurrently instead of serialized, saveUserSettings would
    // already have been called a second time by now, even though the
    // first request hasn't settled.
    await new Promise((resolve) => setTimeout(resolve, 1200))
    expect(saveUserSettings).toHaveBeenCalledTimes(1)

    // Only once the first request settles should the second one's actual
    // network call fire.
    resolveFirstSave()
    await waitFor(() => expect(saveUserSettings).toHaveBeenCalledTimes(2), { timeout: 2000 })
    expect(saveUserSettings).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ anthropic_api_key: 'second-edit' })
    )

    resolveSecondSave()
  })

  it('suppresses a stale error from a save already superseded by a newer edit', async () => {
    let rejectFirstSave: (err: Error) => void = () => {}
    let resolveSecondSave: () => void = () => {}
    const firstSave = new Promise<void>((_resolve, reject) => { rejectFirstSave = reject })
    const secondSave = new Promise<void>((resolve) => { resolveSecondSave = resolve })
    saveUserSettings.mockImplementationOnce(() => firstSave)
    saveUserSettings.mockImplementationOnce(() => secondSave)

    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())

    const input = screen.getByLabelText('Anthropic API key')
    fireEvent.change(input, { target: { value: 'first-edit' } })
    await waitFor(() => expect(saveUserSettings).toHaveBeenCalledTimes(1), { timeout: 2000 })

    // Trigger a second debounced save while the first request is still in
    // flight -- it's queued behind the first in the chain but hasn't
    // started yet. Wait out the real 800ms debounce so saveUserSettingsNow
    // actually runs and claims sequence #2 before save #1 rejects below --
    // otherwise save #1 would still be the latest sequence and the guard
    // would have nothing to suppress.
    fireEvent.change(input, { target: { value: 'second-edit' } })
    await new Promise((resolve) => setTimeout(resolve, 1000))
    expect(saveUserSettings).toHaveBeenCalledTimes(1) // save #2 is queued, not yet fired

    // Reject the now-superseded first save and let its catch block and
    // save #2's handoff run to completion.
    rejectFirstSave(new Error('stale save failed'))
    await new Promise((resolve) => setTimeout(resolve, 100))

    // The first save's error must never reach the screen -- a newer save
    // (#2) has already superseded it.
    expect(screen.queryByText('stale save failed')).not.toBeInTheDocument()

    resolveSecondSave()
    await waitFor(() => expect(saveUserSettings).toHaveBeenCalledTimes(2), { timeout: 2000 })
    expect(screen.queryByText('stale save failed')).not.toBeInTheDocument()
  })
})
