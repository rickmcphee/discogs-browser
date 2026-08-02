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

  it('renders anthropic API key field and saves it', async () => {
    render(<Account avatarVersion={0} onAvatarChange={() => {}} />)
    await waitFor(() => expect(getUserSettings).toHaveBeenCalled())
    const input = screen.getByLabelText('Anthropic API key')
    fireEvent.change(input, { target: { value: 'sk-ant-new-key' } })
    fireEvent.click(screen.getAllByRole('button', { name: /Save/i })[0])
    await waitFor(() =>
      expect(saveUserSettings).toHaveBeenCalledWith(
        expect.objectContaining({ anthropic_api_key: 'sk-ant-new-key' })
      )
    )
  })
})
