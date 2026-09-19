import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { usePersistentFlag, usePersistentState } from '../hooks/usePersistentState'

type Mode = 'list' | 'tiles'

const parseMode = (raw: string): Mode | null => (raw === 'list' || raw === 'tiles' ? raw : null)

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('usePersistentState', () => {
  it('restores a stored value its parse recognises', () => {
    localStorage.setItem('mode', 'tiles')
    const { result } = renderHook(() => usePersistentState<Mode>('mode', 'list', parseMode))
    expect(result.current[0]).toBe('tiles')
  })

  it('falls back when the stored value is one this build no longer offers', () => {
    localStorage.setItem('mode', 'carousel')
    const { result } = renderHook(() => usePersistentState<Mode>('mode', 'list', parseMode))
    expect(result.current[0]).toBe('list')
  })

  it('keeps an empty stored string rather than reading it as absent', () => {
    // '' is the artist filter's "All", so a falsy check here would make that
    // selection the one value the hook cannot restore.
    localStorage.setItem('artist', '')
    const { result } = renderHook(() => usePersistentState('artist', 'Pink Floyd', (raw) => raw))
    expect(result.current[0]).toBe('')
  })

  it('writes every later change back', () => {
    const { result } = renderHook(() => usePersistentState<Mode>('mode', 'list', parseMode))
    act(() => result.current[1]('tiles'))
    expect(localStorage.getItem('mode')).toBe('tiles')
  })

  it('does not store a value its own parse would refuse, leaving the last restorable one', () => {
    // What App's tab needs: Account and Notifications are not restorable, and
    // opening one must not throw away the library tab the user came from.
    localStorage.setItem('mode', 'tiles')
    const { result } = renderHook(() => usePersistentState<Mode | 'errand'>('mode', 'list', parseMode))
    act(() => result.current[1]('errand'))
    expect(result.current[0]).toBe('errand')
    expect(localStorage.getItem('mode')).toBe('tiles')
  })

  it('renders with the fallback when reading storage throws', () => {
    vi.spyOn(window.localStorage, 'getItem').mockImplementation(() => {
      throw new Error('site data blocked')
    })
    const { result } = renderHook(() => usePersistentState<Mode>('mode', 'list', parseMode))
    expect(result.current[0]).toBe('list')
  })

  it('keeps working for this visit when writing storage throws', () => {
    vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
      throw new Error('quota exceeded')
    })
    const { result } = renderHook(() => usePersistentState<Mode>('mode', 'list', parseMode))
    act(() => result.current[1]('tiles'))
    expect(result.current[0]).toBe('tiles')
  })
})

describe('usePersistentFlag', () => {
  it('restores a stored flag', () => {
    localStorage.setItem('cheapest', 'true')
    const { result } = renderHook(() => usePersistentFlag('cheapest'))
    expect(result.current[0]).toBe(true)
  })

  it('falls back for anything that is not a stored boolean', () => {
    localStorage.setItem('cheapest', 'yes')
    const { result } = renderHook(() => usePersistentFlag('cheapest'))
    expect(result.current[0]).toBe(false)
  })

  it('stores both directions as the strings stockCheapest already holds', () => {
    const { result } = renderHook(() => usePersistentFlag('cheapest'))
    act(() => result.current[1](true))
    expect(localStorage.getItem('cheapest')).toBe('true')
    act(() => result.current[1](false))
    expect(localStorage.getItem('cheapest')).toBe('false')
  })

  it('honours a fallback of true when nothing is stored', () => {
    const { result } = renderHook(() => usePersistentFlag('cheapest', true))
    expect(result.current[0]).toBe(true)
  })
})
