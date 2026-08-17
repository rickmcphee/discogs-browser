import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import BackendDownScreen from '../views/BackendDownScreen'

describe('BackendDownScreen', () => {
  it("tells the user the server can't be reached and shows a spinner", () => {
    render(<BackendDownScreen />)
    expect(screen.getByText("Can't reach the server. Retrying…")).toBeInTheDocument()
  })

  it('has an accessible live region so screen readers announce the outage', () => {
    render(<BackendDownScreen />)
    expect(screen.getByRole('status')).toBeInTheDocument()
  })
})
