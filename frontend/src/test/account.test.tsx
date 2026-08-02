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
})
