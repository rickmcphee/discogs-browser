import { useEffect, useRef, useState } from 'react'
import type { Crawler, CrawlerGenre } from '../api/types'
import { navButtonClass } from '../styles/buttons'

interface Props {
  crawlers: Crawler[]
  hiddenCrawlerIds: number[]
  onChange: (hiddenCrawlerIds: number[]) => void
}

const GENRES: { key: CrawlerGenre; label: string }[] = [
  { key: 'marketplace', label: 'Marketplace' },
  { key: 'punk', label: 'Punk' },
  { key: 'metal', label: 'Metal' },
  { key: 'rock', label: 'Rock' },
  { key: 'pop', label: 'Pop' },
]

function FilterCheckbox({ label, checked, indeterminate, onToggle }: {
  label: string
  checked: boolean
  indeterminate: boolean
  onToggle: () => void
}) {
  return (
    <label className="flex items-center gap-2 py-1 cursor-pointer text-gray-200 hover:text-white">
      <input
        type="checkbox"
        aria-label={label}
        checked={checked}
        ref={(el) => { if (el) el.indeterminate = indeterminate }}
        onChange={onToggle}
        className="accent-white"
      />
      {label}
    </label>
  )
}

function SourceFilter({ crawlers, hiddenCrawlerIds, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onMouseDown(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onMouseDown)
    return () => document.removeEventListener('mousedown', onMouseDown)
  }, [open])

  const byGenre = new Map<CrawlerGenre, Crawler[]>()
  for (const c of crawlers) {
    const list = byGenre.get(c.genre) ?? []
    list.push(c)
    byGenre.set(c.genre, list)
  }

  function genreState(genre: CrawlerGenre): 'all' | 'none' | 'mixed' {
    const list = byGenre.get(genre) ?? []
    if (list.length === 0) return 'all'
    const hiddenCount = list.filter((c) => hiddenCrawlerIds.includes(c.id)).length
    if (hiddenCount === 0) return 'all'
    if (hiddenCount === list.length) return 'none'
    return 'mixed'
  }

  function toggleGenre(genre: CrawlerGenre) {
    const ids = (byGenre.get(genre) ?? []).map((c) => c.id)
    if (genreState(genre) === 'all') {
      onChange([...new Set([...hiddenCrawlerIds, ...ids])])
    } else {
      onChange(hiddenCrawlerIds.filter((id) => !ids.includes(id)))
    }
  }

  function toggleStore(crawlerId: number) {
    onChange(
      hiddenCrawlerIds.includes(crawlerId)
        ? hiddenCrawlerIds.filter((id) => id !== crawlerId)
        : [...hiddenCrawlerIds, crawlerId]
    )
  }

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={`px-3 py-1.5 text-sm font-medium ${navButtonClass(open || hiddenCrawlerIds.length > 0)}`}
      >
        Source
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-72 max-h-[28rem] overflow-y-auto rounded-xl border border-gray-700 bg-gray-900 shadow-xl z-50 p-3 text-sm">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs uppercase tracking-wider text-gray-500">By genre</span>
            <button type="button" onClick={() => onChange([])} className="text-xs text-gray-400 hover:text-white">
              Show all
            </button>
          </div>
          {GENRES.map(({ key, label }) => (
            <FilterCheckbox
              key={key}
              label={label}
              checked={genreState(key) === 'all'}
              indeterminate={genreState(key) === 'mixed'}
              onToggle={() => toggleGenre(key)}
            />
          ))}
          <div className="border-t border-gray-800 my-3" />
          <span className="text-xs uppercase tracking-wider text-gray-500">By store</span>
          {crawlers.map((c) => (
            <FilterCheckbox
              key={c.id}
              label={c.site_name}
              checked={!hiddenCrawlerIds.includes(c.id)}
              indeterminate={false}
              onToggle={() => toggleStore(c.id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

export default SourceFilter
