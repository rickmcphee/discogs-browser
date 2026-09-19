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

// A storage method lives on its object's prototype, and the instance takes an
// own-property spy without ever consulting it -- so a spy installed on
// `localStorage` itself silently does nothing, and a test written that way
// passes whether or not the code under test guards anything.
//
// Which prototype that is depends on the environment, so it is asked for
// rather than named: jsdom's own Storage instance keeps its methods on
// `Storage.prototype`, while the MemoryStorage fallback setup.ts installs
// where jsdom leaves localStorage undefined keeps them on its own class
// prototype, which does not inherit from Storage at runtime.
function storagePrototype(): Storage {
  return Object.getPrototypeOf(window.localStorage) as Storage
}

function breakStorage(method: 'getItem' | 'setItem', message: string) {
  vi.spyOn(storagePrototype(), method).mockImplementation(() => {
    throw new Error(message)
  })
}

describe('the storage-failure helper', () => {
  // Twice now a spy has been installed somewhere the code under test never
  // looks, leaving every failure-path test below passing without a throw. This
  // asserts the helper does what its name says, whichever storage the
  // environment provides.
  it('actually makes storage throw', () => {
    breakStorage('getItem', 'boom')
    expect(() => localStorage.getItem('anything')).toThrow('boom')
  })
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
    breakStorage('getItem', 'site data blocked')
    const { result } = renderHook(() => usePersistentState<Mode>('mode', 'list', parseMode))
    expect(result.current[0]).toBe('list')
  })

  it('keeps working for this visit when writing storage throws', () => {
    breakStorage('setItem', 'quota exceeded')
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
