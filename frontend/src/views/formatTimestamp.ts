// Postgres TIMESTAMP (not TIMESTAMPTZ) columns serialize as offsetless ISO
// strings -- `new Date()` on those parses as browser-local time, not UTC.
export function formatServerTimestamp(iso: string): string {
  return new Date(withUtcOffset(iso)).toLocaleString()
}

// Same parse, but relative: "3h ago" reads better than a wall-clock time on a
// feed where what matters is how fresh an entry is, not when exactly it landed.
export function formatRelativeTime(iso: string, now: number = Date.now()): string {
  const elapsed = now - new Date(withUtcOffset(iso)).getTime()
  if (!Number.isFinite(elapsed)) return ''
  const minutes = Math.floor(elapsed / 60_000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}d ago`
  return formatServerTimestamp(iso)
}

function withUtcOffset(iso: string): string {
  const hasOffset = /[zZ]|[+-]\d\d:\d\d$/.test(iso)
  return hasOffset ? iso : `${iso}Z`
}
