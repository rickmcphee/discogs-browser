import { describe, it, expect } from 'vitest'
import { formatRelativeTime, formatServerTimestamp } from '../views/formatTimestamp'

// Postgres TIMESTAMP columns serialize without an offset. Both helpers have to
// read those as UTC -- reading them as browser-local is the bug this exists to
// prevent, and it is invisible in a UTC test environment unless asserted
// against a fixed `now`.
const NOW = Date.parse('2026-08-28T12:00:00Z')

describe('formatRelativeTime', () => {
  it('reads an offsetless server timestamp as UTC', () => {
    expect(formatRelativeTime('2026-08-28T09:00:00', NOW)).toBe('3h ago')
  })

  it('leaves a timestamp that already carries an offset alone', () => {
    expect(formatRelativeTime('2026-08-28T09:00:00Z', NOW)).toBe('3h ago')
    expect(formatRelativeTime('2026-08-28T11:00:00+01:00', NOW)).toBe('2h ago')
  })

  it('scales from minutes through days', () => {
    expect(formatRelativeTime('2026-08-28T11:59:40', NOW)).toBe('just now')
    expect(formatRelativeTime('2026-08-28T11:45:00', NOW)).toBe('15m ago')
    expect(formatRelativeTime('2026-08-26T12:00:00', NOW)).toBe('2d ago')
  })

  it('falls back to an absolute timestamp past a month', () => {
    const old = '2026-01-01T12:00:00'
    expect(formatRelativeTime(old, NOW)).toBe(formatServerTimestamp(old))
  })
})
