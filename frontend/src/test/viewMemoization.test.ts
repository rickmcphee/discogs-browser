import { describe, it, expect } from 'vitest'
import StockBrowser from '../views/StockBrowser'
import Settings from '../views/Settings'
import Account from '../views/Account'
import LogViewer from '../views/LogViewer'

describe('heavy views mounted for the lifetime of the app are memoized', () => {
  it('StockBrowser, Settings, Account, and LogViewer are wrapped in React.memo', () => {
    const memoType = Symbol.for('react.memo')
    expect((StockBrowser as any).$$typeof).toBe(memoType)
    expect((Settings as any).$$typeof).toBe(memoType)
    expect((Account as any).$$typeof).toBe(memoType)
    expect((LogViewer as any).$$typeof).toBe(memoType)
  })
})
