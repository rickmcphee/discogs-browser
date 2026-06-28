import { useState, useEffect, useCallback } from 'react'
import { listScreenshotSessions, screenshotUrl } from '../api/client'
import type { ScreenshotSession, ScreenshotEntry } from '../api/types'

function groupByPath(entries: ScreenshotEntry[]): Record<string, ScreenshotEntry[]> {
  const groups: Record<string, ScreenshotEntry[]> = {}
  for (const entry of entries) {
    // path: session_id/site/release/step.png — group key is site/release
    const parts = entry.path.split('/')
    const key = parts.length >= 3 ? `${parts[1]}/${parts[2]}` : 'other'
    if (!groups[key]) groups[key] = []
    groups[key].push(entry)
  }
  return groups
}

function ScreenshotThumb({ entry }: { entry: ScreenshotEntry }) {
  const url = screenshotUrl(entry.path)
  const step = entry.path.split('/').pop()?.replace('.png', '') ?? ''
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="group block bg-gray-900 border border-gray-700 rounded overflow-hidden hover:border-indigo-500 transition-colors"
      title={entry.url || entry.path}
    >
      <img
        src={url}
        alt={entry.path}
        className="w-full h-32 object-cover object-top"
        loading="lazy"
      />
      <div className="px-2 py-1 flex items-center justify-between">
        <span className="text-xs text-gray-500 font-mono">step {step}</span>
        {entry.url && (
          <span className="text-xs text-gray-600 truncate max-w-32" title={entry.url}>
            {new URL(entry.url).hostname}
          </span>
        )}
      </div>
    </a>
  )
}

function SessionPanel({ session }: { session: ScreenshotSession }) {
  const [expanded, setExpanded] = useState(true)
  const groups = groupByPath(session.entries)

  const date = session.session_id.replace(
    /^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/,
    '$1-$2-$3 $4:$5:$6'
  )

  return (
    <div className="border border-gray-800 rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded((e) => !e)}
        className="w-full flex items-center justify-between px-4 py-3 bg-gray-900 hover:bg-gray-800 transition-colors"
      >
        <span className="text-sm font-medium text-gray-200">{date}</span>
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-500">{session.entries.length} screenshot{session.entries.length !== 1 ? 's' : ''}</span>
          <span className="text-gray-500">{expanded ? '▲' : '▼'}</span>
        </div>
      </button>
      {expanded && (
        <div className="p-4 space-y-6 bg-gray-950">
          {Object.entries(groups).map(([groupKey, entries]) => {
            const [site, release] = groupKey.split('/')
            return (
              <div key={groupKey}>
                <div className="text-xs text-gray-500 mb-2 font-mono">
                  <span className="text-indigo-400">{site}</span>
                  {release && <span className="text-gray-600"> / {release}</span>}
                </div>
                <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 lg:grid-cols-6">
                  {entries.map((entry) => (
                    <ScreenshotThumb key={entry.path} entry={entry} />
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default function DebugView() {
  const [sessions, setSessions] = useState<ScreenshotSession[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await listScreenshotSessions()
      setSessions(data)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm text-gray-400 mt-1">
            Screenshots captured during crawls. Enable in Settings → Debug Screenshot Interval.
          </p>
        </div>
        <button
          onClick={load}
          className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-sm text-gray-300 transition-colors"
        >
          Refresh
        </button>
      </div>

      {loading && (
        <div className="text-gray-500 text-sm">Loading…</div>
      )}
      {error && (
        <div className="text-red-400 text-sm">Error: {error}</div>
      )}
      {!loading && sessions.length === 0 && (
        <div className="text-gray-500 text-sm italic">
          No screenshots yet. Run a price refresh with debug screenshots enabled.
        </div>
      )}
      {sessions.map((s) => (
        <SessionPanel key={s.session_id} session={s} />
      ))}
    </div>
  )
}
