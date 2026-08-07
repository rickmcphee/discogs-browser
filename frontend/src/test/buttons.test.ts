import { describe, it, expect } from 'vitest'
import { navButtonClass, primaryButtonClass, secondaryButtonClass, dismissButtonClass } from '../styles/buttons'

describe('navButtonClass', () => {
  it('returns the active (filled) style when isActive is true', () => {
    expect(navButtonClass(true)).toBe('rounded-full transition-colors bg-white text-gray-950')
  })

  it('returns the inactive (ghost) style when isActive is false', () => {
    expect(navButtonClass(false)).toBe('rounded-full transition-colors text-gray-400 hover:text-white hover:bg-gray-800')
  })
})

describe('primaryButtonClass', () => {
  it('returns the white pill CTA style', () => {
    expect(primaryButtonClass()).toBe('rounded-full bg-white hover:bg-gray-200 active:bg-gray-300 text-gray-950 font-medium transition-colors')
  })
})

describe('secondaryButtonClass', () => {
  it('returns the gray pill style', () => {
    expect(secondaryButtonClass()).toBe('rounded-full bg-gray-700 hover:bg-gray-600 text-white font-medium transition-colors')
  })
})

describe('dismissButtonClass', () => {
  it('returns the ghost pill style with hover background', () => {
    expect(dismissButtonClass()).toBe('rounded-full hover:bg-gray-800 text-gray-400 hover:text-white transition-colors')
  })
})
