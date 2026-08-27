import { useEffect, useState } from 'react'

// One below Tailwind's `md` floor, so exactly one of the two layouts is ever
// active: anything this query matches is styled by the unprefixed classes,
// anything it doesn't is styled by the `md:` ones. Changing this without
// changing the prefix used across the views splits the app into a third state
// that nobody has laid out.
export const MOBILE_QUERY = '(max-width: 767px)'

function matches(query: string): boolean {
  // jsdom leaves matchMedia undefined, and so does anything old enough not to
  // be a phone this app targets. Desktop is the layout that degrades
  // gracefully into a narrow window, so it is the right answer for both.
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia(query).matches
}

export function useMediaQuery(query: string): boolean {
  // Read synchronously rather than in an effect: an effect-based first read
  // paints the desktop tree and swaps it on mount, which on a phone is a
  // visible flash of the layout the user can't operate.
  const [isMatch, setIsMatch] = useState(() => matches(query))

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const list = window.matchMedia(query)
    // The query itself can change between renders, and the state above is only
    // re-derived on mount -- so re-sync here rather than trusting the
    // initialiser's answer for a query it never saw.
    setIsMatch(list.matches)
    const onChange = (event: MediaQueryListEvent) => setIsMatch(event.matches)
    list.addEventListener('change', onChange)
    return () => list.removeEventListener('change', onChange)
  }, [query])

  return isMatch
}

export function useIsMobile(): boolean {
  return useMediaQuery(MOBILE_QUERY)
}
