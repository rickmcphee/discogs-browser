import { useEffect, useRef, useState } from 'react'
import { getStockStats } from '../api/client'
import type { LibraryScope, StockStats as StockStatsData } from '../api/types'
import { OTHER_COLOR, OTHER_KEY, toSlices } from './stockSlices'
import { navButtonClass } from '../styles/buttons'
import { useIsMobile } from '../hooks/useMediaQuery'
import Sheet from './Sheet'
import Donut from './Donut'

interface Props {
  search?: string
  artist?: string
  libraryScope?: LibraryScope
  recommended?: boolean
  saved?: boolean
  overlapped?: boolean
  hiddenCrawlerIds: number[]
  /** Ticks when the underlying rows may have changed (a stock sync, a save). */
  refreshKey?: number
  disabled?: boolean
}

function share(value: number, total: number): string {
  if (total === 0) return '0%'
  const pct = (value / total) * 100
  return pct < 1 ? '<1%' : `${Math.round(pct)}%`
}

function StockStats({
  search, artist, libraryScope, recommended = false, saved = false, overlapped = false,
  hiddenCrawlerIds, refreshKey, disabled = false,
}: Props) {
  const isMobile = useIsMobile()
  const [open, setOpen] = useState(false)
  const [stats, setStats] = useState<StockStatsData | null>(null)
  const [error, setError] = useState(false)
  const [hovered, setHovered] = useState<string | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // Same outside-click dismissal as the Source filter beside it; the mobile
    // sheet brings its own backdrop.
    if (!open || isMobile) return
    function onMouseDown(e: MouseEvent | TouchEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onMouseDown)
    document.addEventListener('touchstart', onMouseDown)
    return () => {
      document.removeEventListener('mousedown', onMouseDown)
      document.removeEventListener('touchstart', onMouseDown)
    }
  }, [open, isMobile])

  // Fetched only while the panel is open -- this is a breakdown of what the
  // table beside it already shows, so nobody pays for it until they ask. The
  // filter props are in the dependency list, so a filter changed with the
  // panel open refetches rather than leaving a stale breakdown on screen.
  useEffect(() => {
    // Closing discards what was fetched. Filters move while the panel is
    // shut, and reopening onto the previous breakdown would show numbers for
    // a view that no longer exists until the new request lands.
    if (!open) {
      setStats(null)
      setError(false)
      setHovered(null)
      return
    }
    let latest = true
    getStockStats({ search, artist, libraryScope, recommended, saved, overlapped, hiddenCrawlerIds })
      .then((next) => {
        if (!latest) return
        setStats(next)
        setError(false)
      })
      .catch(() => { if (latest) setError(true) })
    return () => { latest = false }
  }, [open, search, artist, libraryScope, recommended, saved, overlapped, hiddenCrawlerIds, refreshKey])

  const sources = stats?.sources ?? null
  const total = stats?.total ?? 0
  const slices = sources ? toSlices(sources) : []
  // One rule for which wedge a source belongs to, derived from the slices
  // themselves rather than restated: a source the ring folded into the tail
  // has no slice of its own, so it wears the neutral and lights up with it.
  const sliceColors = new Map(slices.map((s) => [s.key, s.color]))
  const sliceKeyFor = (crawlerId: number) =>
    sliceColors.has(String(crawlerId)) ? String(crawlerId) : OTHER_KEY

  const panel = (
    <>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs uppercase tracking-wider text-gray-500">Items by source</span>
        {stats && <span className="text-xs text-gray-400">{total.toLocaleString()} total</span>}
      </div>
      {error && <div className="py-4 text-sm text-gray-400 italic">Couldn’t load the breakdown.</div>}
      {!error && sources === null && <div className="py-4 text-sm text-gray-400 italic">Loading…</div>}
      {!error && sources !== null && sources.length === 0 && (
        <div className="py-4 text-sm text-gray-400 italic">No items in stock for this filter.</div>
      )}
      {!error && sources !== null && slices.length > 0 && (
        <>
          <div className="flex justify-center py-1">
            <Donut
              segments={slices.map((s) => ({
                key: s.key,
                value: s.value,
                color: s.color,
                title: `${s.label}: ${s.value.toLocaleString()} items (${share(s.value, total)})`,
              }))}
              centreValue={total.toLocaleString()}
              centreLabel={total === 1 ? 'item' : 'items'}
              ariaLabel="In-stock items by source"
              emphasised={hovered}
              onHover={setHovered}
            />
          </div>
          {/* Every source, named, with its exact count -- the ring folds its
              tail into one wedge, this doesn't. It is also what keeps the
              breakdown readable without relying on colour. The height cap is
              desktop-only: the anchored dropdown has no scroll of its own,
              while the mobile sheet already scrolls and a nested scroll
              region inside it is just something else to get stuck in. */}
          <ul className="mt-2 md:max-h-56 md:overflow-y-auto">
            {sources.map((s) => {
              const key = sliceKeyFor(s.crawler_id)
              const color = sliceColors.get(key) ?? OTHER_COLOR
              return (
                <li
                  key={s.crawler_id}
                  onMouseEnter={() => setHovered(key)}
                  onMouseLeave={() => setHovered(null)}
                  className={`flex items-center gap-2 py-1 ${hovered === key ? 'text-white' : 'text-gray-200'}`}
                >
                  <span className="w-2 h-2 rounded-full shrink-0" style={{ background: color }} />
                  <span className="truncate" title={s.site_name}>{s.site_name}</span>
                  <span className="ml-auto shrink-0 tabular-nums text-gray-400">
                    {s.count.toLocaleString()}
                    <span className="ml-1.5 text-gray-500">{share(s.count, total)}</span>
                  </span>
                </li>
              )
            })}
          </ul>
        </>
      )}
    </>
  )

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        disabled={disabled}
        aria-expanded={open}
        title={disabled ? 'Loading…' : undefined}
        className={`h-11 px-3 text-sm font-medium disabled:opacity-40 disabled:cursor-not-allowed md:h-auto md:py-1.5 ${navButtonClass(open)}`}
      >
        Stats
      </button>
      {open && !disabled && !isMobile && (
        <div className="absolute right-0 mt-2 w-72 rounded-xl border border-gray-700 bg-gray-900 shadow-xl z-50 p-3 text-sm">
          {panel}
        </div>
      )}
      {isMobile && (
        <Sheet open={open && !disabled} onClose={() => setOpen(false)} label="In-stock items by source">
          <div className="p-3 pb-4 text-sm">{panel}</div>
        </Sheet>
      )}
    </div>
  )
}

export default StockStats
