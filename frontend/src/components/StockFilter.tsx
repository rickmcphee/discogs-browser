import { useEffect, useRef, useState } from 'react'
import { navButtonClass } from '../styles/buttons'
import { useIsMobile } from '../hooks/useMediaQuery'
import Sheet from './Sheet'

interface Option {
  value: string
  label: string
}

// Collection and Wantlist last: they were the Track tab's own filters until
// that tab folded into this one, and they narrow to the user's library where
// the four before them narrow the store's shelf.
const STOCK_FILTER_OPTIONS: Option[] = [
  { value: 'all', label: 'All' },
  { value: 'recommended', label: 'Recommended' },
  { value: 'saved', label: 'Saved' },
  { value: 'overlapped', label: 'Overlapped' },
  { value: 'collection', label: 'Collection' },
  { value: 'wantlist', label: 'Wantlist' },
]

interface Props {
  filter: string
  onFilterChange: (value: string) => void
  recommendedAvailable?: boolean
  cheapest?: boolean
  onCheapestChange?: (value: boolean) => void
}

// The Store tab's row-set filters behind one trigger, in the same
// anchored-dropdown / sheet shape as Source and Stats beside it. The filter
// used to be a bare <select> with the Cheapest checkbox alongside, which was
// the toolbar's last free width; a panel has room for the next filter too.
// The trigger reads its own state so nothing a glance at the <select> gave
// is lost: "Filter: All", "Filter: Saved", "Filter: Saved · Cheapest".
function StockFilter({
  filter, onFilterChange, recommendedAvailable = false,
  cheapest = false, onCheapestChange,
}: Props) {
  const isMobile = useIsMobile()
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // The mobile sheet has its own backdrop; only the desktop dropdown needs
    // an outside-click to dismiss it.
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

  const current = STOCK_FILTER_OPTIONS.find((o) => o.value === filter) ?? STOCK_FILTER_OPTIONS[0]
  const active = filter !== 'all' || cheapest

  const panel = (
    <>
      <div role="radiogroup" aria-label="Show">
        <span className="block text-xs uppercase tracking-wider text-gray-500 mb-1">Show</span>
        {STOCK_FILTER_OPTIONS.map((o) => {
          const disabled = o.value === 'recommended' && !recommendedAvailable
          return (
            <label
              key={o.value}
              className={`flex items-center gap-2 py-1 ${disabled ? 'text-gray-600 cursor-not-allowed' : 'cursor-pointer text-gray-200 hover:text-white'}`}
            >
              <input
                type="radio"
                name="stock-filter"
                value={o.value}
                checked={current.value === o.value}
                disabled={disabled}
                onChange={() => onFilterChange(o.value)}
                className="accent-white"
              />
              {o.label}
            </label>
          )
        })}
      </div>
      <div className="border-t border-gray-800 my-3" />
      <label className="flex items-center gap-2 py-1 cursor-pointer text-gray-200 hover:text-white">
        <input
          type="checkbox"
          checked={cheapest}
          onChange={(e) => onCheapestChange?.(e.target.checked)}
          className="accent-white"
        />
        Cheapest
      </label>
      <p className="mt-1 text-xs text-gray-500">
        One row per record: the lowest-priced store that has it.
      </p>
    </>
  )

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className={`h-11 max-w-48 truncate px-3 text-sm font-medium md:h-auto md:py-1.5 ${navButtonClass(open || active)}`}
      >
        Filter: {current.label}{cheapest ? ' · Cheapest' : ''}
      </button>
      {open && !isMobile && (
        <div className="absolute right-0 mt-2 w-56 rounded-xl border border-gray-700 bg-gray-900 shadow-xl z-50 p-3 text-sm text-left">
          {panel}
        </div>
      )}
      {isMobile && (
        <Sheet open={open} onClose={() => setOpen(false)} label="Filter">
          <div className="p-3 pb-4 text-sm">{panel}</div>
        </Sheet>
      )}
    </div>
  )
}

export default StockFilter
