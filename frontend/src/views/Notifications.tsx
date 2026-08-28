import { useCallback, useEffect, useRef, useState } from 'react'
import { getNotifications } from '../api/client'
import type { PriceDropNotification } from '../api/types'
import { formatPrice } from './formatPrice'
import { formatRelativeTime } from './formatTimestamp'

interface Props {
  // Bumped by App on every SSE tick that could have produced a price drop, so
  // an open tab picks up new ones without a reload.
  generation: number
  // Called with the newest id in each response. App owns the bell's dot, so it
  // is App that writes the read watermark -- passing the response's own
  // latest_id keeps the two from disagreeing.
  onLoaded: (latestId: number | null) => void
}

export default function Notifications({ generation, onLoaded }: Props) {
  const [items, setItems] = useState<PriceDropNotification[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // Captured from the first response of this visit and then left alone. Rows
  // above it keep their accent for the rest of the visit even though opening
  // the tab has already marked them read, so the user can still see what was
  // new when they arrived.
  const unreadFloor = useRef<number | null>(null)
  // formatRelativeTime is computed at render, and nothing else re-renders this
  // view on a schedule -- so a tab left open would keep saying "just now" hours
  // later. A minute is the resolution the format itself has, so ticking faster
  // would only cost renders.
  const [, setElapsedTick] = useState(0)

  useEffect(() => {
    const timer = setInterval(() => setElapsedTick(t => t + 1), 60_000)
    return () => clearInterval(timer)
  }, [])

  const load = useCallback(async (isLatest: () => boolean) => {
    try {
      const data = await getNotifications()
      if (!isLatest()) return
      if (unreadFloor.current === null) unreadFloor.current = data.last_read_id
      setItems(data.items)
      setError(null)
      onLoaded(data.latest_id)
    } catch (e: any) {
      if (isLatest()) setError(e?.message || 'Could not load notifications')
    } finally {
      if (isLatest()) setLoading(false)
    }
  }, [onLoaded])

  useEffect(() => {
    let current = true
    load(() => current)
    return () => { current = false }
  }, [load, generation])

  if (loading && items.length === 0) {
    return <p className="p-6 text-sm text-gray-400">Loading notifications…</p>
  }

  if (error && items.length === 0) {
    return <p className="p-6 text-sm text-red-400">{error}</p>
  }

  return (
    <div className="p-4 md:p-6 max-w-3xl">
      <h1 className="text-lg font-semibold text-white mb-4">Notifications</h1>
      {items.length === 0 ? (
        <p className="text-sm text-gray-400">
          Nothing yet. Save an item on the Store tab and you'll be told here when
          it turns up cheaper than any price currently known for it.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((item) => {
            const isUnread = item.id > (unreadFloor.current ?? 0)
            return (
              <li key={item.id}>
                <a
                  href={item.url}
                  target="_blank"
                  rel="noreferrer"
                  data-unread={isUnread ? 'true' : 'false'}
                  className={`flex items-start gap-3 rounded-lg border p-3 transition-colors ${
                    isUnread
                      ? 'border-gray-700 border-l-4 border-l-green-500 bg-gray-900'
                      : 'border-gray-800 bg-gray-900/50 hover:bg-gray-900'
                  }`}
                >
                  {item.cover_image_url ? (
                    <img src={item.cover_image_url} alt="" className="w-12 h-12 shrink-0 object-cover rounded" />
                  ) : (
                    <div className="w-12 h-12 shrink-0 bg-gray-800 rounded" />
                  )}
                  <div className="min-w-0 flex-1">
                    {/* The accent border says "new" to sighted users, and
                        the bell's dot is gone the moment the tab is
                        opened -- so without this the distinction exists
                        only in paint. Part of the link's accessible name,
                        not a title attribute, so it is announced when the
                        link is reached. */}
                    {isUnread && <span className="sr-only">New. </span>}
                    <div className="truncate text-sm text-gray-200">{item.artist}</div>
                    <div className="truncate text-sm text-gray-300">{item.title}</div>
                    <div className="mt-1 text-sm">
                      <span className="font-medium text-green-400">
                        {formatPrice(item.price, item.currency)}
                      </span>
                      {' '}
                      <span className="text-gray-500 line-through">
                        {formatPrice(item.previous_best, item.currency)}
                      </span>
                      {' '}
                      <span className="text-gray-400">at {item.source}</span>
                    </div>
                  </div>
                  <span className="shrink-0 text-xs text-gray-500">{formatRelativeTime(item.created_at)}</span>
                </a>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
