import { selectClass } from '../styles/inputs'
import { navButtonClass } from '../styles/buttons'

export interface SortOption<T extends string> {
  field: T
  label: string
}

interface Props<T extends string> {
  options: SortOption<T>[]
  sort: T
  order: 'asc' | 'desc'
  /** The same handler the desktop column headers call: selecting the current
      field flips the direction, selecting a new one starts ascending. */
  onSort: (field: T) => void
}

// The card list has no column headers, and sorting is not decoration on a
// 250-row page. Both controls write through `onSort`, so mobile and desktop
// share one sort model rather than growing a second one.
export default function MobileSort<T extends string>({ options, sort, order, onSort }: Props<T>) {
  const current = options.find((o) => o.field === sort)
  return (
    <div className="flex items-center gap-1">
      <select
        aria-label="Sort by"
        value={sort}
        onChange={(e) => {
          const next = e.target.value as T
          if (next !== sort) onSort(next)
        }}
        className={`px-3 py-2 text-sm ${selectClass()}`}
      >
        {/* "By artist", not "Artist" -- the artist *filter* sits next to this
            on the same row, and two controls both reading "Artist" is a
            coin-flip for the reader. */}
        {options.map((o) => (
          <option key={o.field} value={o.field}>By {o.label.toLowerCase()}</option>
        ))}
      </select>
      <button
        type="button"
        onClick={() => onSort(sort)}
        aria-label={`Sort ${order === 'asc' ? 'descending' : 'ascending'} by ${current?.label ?? sort}`}
        className={`min-w-11 h-11 px-3 ${navButtonClass(false)}`}
      >
        {order === 'asc' ? '↑' : '↓'}
      </button>
    </div>
  )
}
