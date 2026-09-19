import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from 'react'

// The selections the app restores on the next visit: the tab, the filters, the
// sort, list vs tiles. They live in localStorage, so they describe the browser
// rather than the account -- the phone wants tiles where the desktop wants the
// table, which a per-user setting would fight on every switch. A preference
// that has to follow the user belongs on the server instead; the source filter
// moved there for exactly that reason.
//
// Read and write are one call rather than the initialiser/effect pair these
// used to be written as, because a stored value is *input*: it was last
// written by whichever build of the app the user last visited, possibly one
// whose options no longer exist. Every caller passes a `parse` that has to
// recognise the string before it becomes state, so there is no shape of this
// hook that trusts it. `stockFilter_store` learnt that the hard way, still
// holding a filter name in browsers after the filter had been renamed.

// Storage is not always there to be read: Safari's private mode, a browser
// configured to block site data and a full quota all throw rather than
// returning null. A throw inside a useState initialiser takes down the whole
// app, so every direction swallows it -- a preference that does not survive
// the visit is a far smaller failure than a blank page, and there is nothing
// the user could do about it either way.
//
// Exported for the values App keeps outside this hook -- the dismissed banner
// ids and the view-as-user toggle, which are not selections restored into a
// control and are read and written from event handlers. They go through the
// same guards because one unguarded read left in a render path is the blank
// page all over again.
export function readStored(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

export function writeStored(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    // Ignored: the selection still works for as long as this tab is open.
  }
}

export function removeStored(key: string): void {
  try {
    localStorage.removeItem(key)
  } catch {
    // Ignored, as above.
  }
}

/**
 * State that survives a restart, as long as the stored string is still one
 * this build recognises.
 *
 * `parse` returns null for a value it does not recognise, and `fallback` is
 * used instead -- via `??`, not `||`, since `''` is a meaningful stored value
 * (the artist filter's "All").
 *
 * Storage is *read* once, on mount, so `key` is expected to be fixed for the
 * component's lifetime -- which holds for every caller here (a literal, or a
 * `scope` prop App pins per mounted instance). A key that changed later would
 * write the current value under the new name without reading what is stored
 * there. `parse` itself is not mount-only: it is called again on every render
 * to gate the write below, which is what lets StockBrowser's sort parse close
 * over the filter that is active now rather than the one it mounted under.
 *
 * Writes go through `parse` too, so a value the hook would refuse to restore
 * is never stored over one it would. App's `view` needs that -- it also holds
 * the errands (Account, Notifications), which are deliberately not restorable,
 * and while one of those is open the stored tab stays the library tab the user
 * was last on. For the callers whose state cannot hold a value their own
 * `parse` rejects, the gate is a no-op.
 */
export function usePersistentState<T extends string>(
  key: string,
  fallback: T,
  parse: (stored: string) => T | null,
): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => {
    const stored = readStored(key)
    return (stored === null ? null : parse(stored)) ?? fallback
  })
  // Asked during render with the current `parse`, rather than inside the
  // effect: StockBrowser's sort parse closes over the active filter, and a
  // closure captured on mount would answer for a filter the user has left.
  const storable = parse(value) !== null
  useEffect(() => {
    if (storable) writeStored(key, value)
  }, [key, value, storable])
  return [value, setValue]
}

/**
 * The same, for a selection that is on or off. A flag is an enum of two
 * strings rather than a second serialiser, and `'true'`/`'false'` is what
 * `stockCheapest` already holds.
 */
export function usePersistentFlag(
  key: string,
  fallback = false,
): [boolean, (value: boolean) => void] {
  const [stored, setStored] = usePersistentState<'true' | 'false'>(
    key,
    fallback ? 'true' : 'false',
    (raw) => (raw === 'true' || raw === 'false' ? raw : null),
  )
  const set = useCallback((value: boolean) => setStored(value ? 'true' : 'false'), [setStored])
  return [stored === 'true', set]
}
