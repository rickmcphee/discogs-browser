import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import InviteCodeScreen from '../views/InviteCodeScreen'

const { redeemInvite } = vi.hoisted(() => ({
  redeemInvite: vi.fn(),
}))

vi.mock('../api/client', () => ({ redeemInvite }))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('InviteCodeScreen', () => {
  it('submits the entered code with the given signup token and calls onRedeemed on success', async () => {
    redeemInvite.mockResolvedValue(undefined)
    const onRedeemed = vi.fn()
    render(<InviteCodeScreen signupToken="signup-token-1" onRedeemed={onRedeemed} />)

    fireEvent.change(screen.getByPlaceholderText(/invite code/i), { target: { value: 'INVITE123' } })
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))

    await waitFor(() => expect(redeemInvite).toHaveBeenCalledWith('signup-token-1', 'INVITE123'))
    await waitFor(() => expect(onRedeemed).toHaveBeenCalled())
  })

  it('shows an error and does not call onRedeemed when redemption fails', async () => {
    redeemInvite.mockRejectedValue(new Error('Invalid or already-used invite code'))
    const onRedeemed = vi.fn()
    render(<InviteCodeScreen signupToken="signup-token-1" onRedeemed={onRedeemed} />)

    fireEvent.change(screen.getByPlaceholderText(/invite code/i), { target: { value: 'BAD' } })
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))

    await waitFor(() => expect(screen.getByText(/invalid or already-used/i)).toBeInTheDocument())
    expect(onRedeemed).not.toHaveBeenCalled()
  })
})
