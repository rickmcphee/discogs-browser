import { useState } from 'react'
import { navButtonClass } from '../styles/buttons'
import Sheet from './Sheet'

interface Props {
  artists: string[]
  selected: string
  onSelect: (artist: string) => void
}

// The list itself is identical in both layouts; only where it hangs differs.
// Two exports rather than one branching component because the sidebar and the
// trigger belong at different points in the tree — the sidebar beside the
// content column, the trigger inside its toolbar.
function ArtistOptions({ artists, selected, onSelect, itemClass }: Props & { itemClass: string }) {
  return (
    <>
      <button
        onClick={() => onSelect('')}
        className={`shrink-0 text-left ${itemClass} ${navButtonClass(!selected)}`}
      >
        All
      </button>
      {artists.map((a) => (
        <button
          key={a}
          onClick={() => onSelect(a)}
          className={`shrink-0 text-left truncate ${itemClass} ${navButtonClass(selected === a)}`}
        >
          {a}
        </button>
      ))}
    </>
  )
}

export function ArtistSidebar(props: Props) {
  return (
    <aside className="w-48 bg-gray-900 border-r border-gray-800 flex flex-col shrink-0 min-h-0">
      <div className="px-3 py-2 text-xs font-medium text-gray-500 uppercase tracking-wider border-b border-gray-800 shrink-0">Artist</div>
      <div className="flex flex-col gap-2 overflow-y-auto p-3">
        <ArtistOptions {...props} itemClass="text-sm px-2 py-1" />
      </div>
    </aside>
  )
}

// The trigger names the current selection rather than showing a bare icon: a
// filter you can only see by opening something is a filter users blame the
// data for.
export function ArtistSheetButton(props: Props) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-expanded={open}
        className={`h-11 max-w-40 truncate px-3 text-sm font-medium ${navButtonClass(Boolean(props.selected))}`}
      >
        Artist: {props.selected || 'All'}
      </button>
      <Sheet open={open} onClose={() => setOpen(false)} label="Filter by artist">
        <div className="flex flex-col gap-1 p-3 pb-4">
          <ArtistOptions
            {...props}
            onSelect={(artist) => { props.onSelect(artist); setOpen(false) }}
            itemClass="text-base px-3 py-3"
          />
        </div>
      </Sheet>
    </>
  )
}
