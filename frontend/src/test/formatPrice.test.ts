import { describe, it, expect } from 'vitest'
import { formatPrice } from '../views/formatPrice'

describe('formatPrice', () => {
  it('renders USD exactly as the old hardcoded template did', () => {
    expect(formatPrice(27.99, 'USD')).toBe('$27.99')
    expect(formatPrice(5, 'USD')).toBe('$5.00')
  })

  it('renders EUR with its own symbol', () => {
    // The live regression this helper exists for is jetglowrecordings.py's:
    // its EUR rows render as "$27.99" today. SPV would have compounded it, but
    // has never run against the live feed, so no SPV row has rendered at all.
    // An earlier version of this comment credited the bug to SPV; corrected
    // alongside the same overclaim in the design doc and formatPrice.ts.
    expect(formatPrice(27.99, 'EUR')).toBe('€27.99')
  })

  it('treats a null currency as USD, so pre-existing rows are unchanged', () => {
    expect(formatPrice(27.99, null)).toBe('$27.99')
  })

  it('is case-insensitive about the currency code', () => {
    expect(formatPrice(27.99, 'eur')).toBe('€27.99')
  })

  it('suffixes an unmapped code rather than guessing a symbol', () => {
    expect(formatPrice(27.99, 'SEK')).toBe('27.99 SEK')
  })
})
