import { describe, it, expect } from 'vitest'
import { textInputClass, selectClass } from '../styles/inputs'

describe('textInputClass', () => {
  it('returns the pill input style with the default border', () => {
    expect(textInputClass()).toBe('rounded-full bg-gray-800 border text-white placeholder-gray-500 focus:outline-none focus:border-gray-400 transition-colors border-gray-700')
  })

  it('returns a red border when invalid', () => {
    expect(textInputClass(true)).toBe('rounded-full bg-gray-800 border text-white placeholder-gray-500 focus:outline-none focus:border-gray-400 transition-colors border-red-500')
  })
})

describe('selectClass', () => {
  it('matches the pill input style', () => {
    expect(selectClass()).toBe('rounded-full bg-gray-800 border text-white placeholder-gray-500 focus:outline-none focus:border-gray-400 transition-colors border-gray-700')
  })
})
