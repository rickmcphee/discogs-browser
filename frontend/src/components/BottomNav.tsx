interface Tab<T extends string> {
  view: T
  label: string
  icon: 'collection' | 'wantlist' | 'store' | 'track'
}

interface Props<T extends string> {
  tabs: Tab<T>[]
  active: T
  onSelect: (view: T) => void
}

function TabIcon({ name }: { name: Tab<string>['icon'] }) {
  const common = {
    width: 22, height: 22, viewBox: '0 0 24 24', fill: 'none',
    stroke: 'currentColor', strokeWidth: 1.6,
    strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
  }
  switch (name) {
    case 'collection':
      return (
        <svg {...common} aria-hidden="true">
          <circle cx="12" cy="12" r="9" />
          <circle cx="12" cy="12" r="2.5" />
        </svg>
      )
    case 'wantlist':
      return (
        <svg {...common} aria-hidden="true">
          <path d="M12 20s-7-4.4-7-9a4 4 0 0 1 7-2.6A4 4 0 0 1 19 11c0 4.6-7 9-7 9Z" />
        </svg>
      )
    case 'store':
      return (
        <svg {...common} aria-hidden="true">
          <path d="M4 8h16l-1 12H5L4 8Z" />
          <path d="M9 8V6a3 3 0 0 1 6 0v2" />
        </svg>
      )
    case 'track':
      return (
        <svg {...common} aria-hidden="true">
          <path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6-10-6-10-6Z" />
          <circle cx="12" cy="12" r="2.5" />
        </svg>
      )
  }
}

// The four library tabs, thumb-height, on every mobile screen. A flow child of
// the shell's flex column rather than a fixed bar: the shell is already
// viewport-height, so this shrinks <main> by exactly its own height and no
// scroll container needs matching bottom padding.
export default function BottomNav<T extends string>({ tabs, active, onSelect }: Props<T>) {
  return (
    <nav
      aria-label="Sections"
      className="shrink-0 border-t border-gray-800 bg-gray-900 pb-safe px-safe"
    >
      <div className="flex h-14 items-stretch">
        {tabs.map((tab) => {
          const isActive = tab.view === active
          return (
            <button
              key={tab.view}
              type="button"
              onClick={() => onSelect(tab.view)}
              aria-current={isActive ? 'page' : undefined}
              className={`flex flex-1 flex-col items-center justify-center gap-0.5 transition-colors ${
                isActive ? 'text-white' : 'text-gray-500'
              }`}
            >
              <TabIcon name={tab.icon} />
              <span className="text-[11px] font-medium leading-none">{tab.label}</span>
            </button>
          )
        })}
      </div>
    </nav>
  )
}
