import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import LoginScreen from '../views/LoginScreen'

vi.mock('../api/client', () => ({
  discogsLoginUrl: () => '/api/auth/discogs/start',
}))

describe('LoginScreen', () => {
  it('renders a Continue with Discogs link pointing at the OAuth start endpoint', () => {
    render(<LoginScreen />)
    const link = screen.getByRole('link', { name: /continue with discogs/i })
    expect(link).toHaveAttribute('href', '/api/auth/discogs/start')
  })

  it('does not render a password field', () => {
    render(<LoginScreen />)
    expect(screen.queryByPlaceholderText(/password/i)).not.toBeInTheDocument()
  })
})
