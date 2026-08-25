import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getQueueSummary, getQueueNext } from '../api/client'
import type { QueueSummary, QueueCrawlerSummary, QueueNextItem } from '../api/types'

const POLL_MS = 10_000

// A single-hue ordinal ramp, light for work in flight through dark for work
// that is stuck, validated as an ordinal ramp against this app's bg-gray-950
// surface. Not a categorical palette: these are three states of one quantity,
// not three identities.
const STATE_COLORS = {
  in_progress: '#86b6ef',
  claimable: '#3987e5',
  held: '#184f95',
} as const

// Reserved status colors, only ever shown beside their own label -- the color
// never carries the meaning alone.
const STATUS_CRITICAL = '#d03b3b'
const STATUS_WARNING = '#fab219'

type StateKey = keyof typeof STATE_COLORS

function unitsInState(crawler: QueueCrawlerSummary, state: StateKey): number {
  if (state === 'held') return crawler.held_units
  if (state === 'claimable') return crawler.claimable_units
  return crawler.in_progress_units
}

const STATE_LABELS: Record<StateKey, string> = {
  in_progress: 'In progress',
  claimable: 'Claimable',
  held: 'Held',
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`
  return `${(seconds / 86400).toFixed(1)}d`
}

function StatTile({ label, value, hint, accent }: {
  label: string
  value: string
  hint?: string
  accent?: string
}) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg px-4 py-3 min-w-36">
      <div className="flex items-center gap-1.5 text-xs text-gray-500">
        {accent && <span className="w-2 h-2 rounded-full shrink-0" style={{ background: accent }} />}
        {label}
      </div>
      <div className="text-2xl text-gray-100 mt-1">{value}</div>
      {hint && <div className="text-xs text-gray-400 mt-0.5">{hint}</div>}
    </div>
  )
}

// Hand-rolled because the frontend has no charting dependency and this needs
// no more than stroke-dasharray on three concentric arcs of one circle.
function StateDonut({ segments, centreValue, centreLabel, selected, onSelect }: {
  segments: { key: StateKey; value: number }[]
  centreValue: string
  centreLabel: string
  selected: StateKey | null
  onSelect: (key: StateKey | null) => void
}) {
  const total = segments.reduce((sum, s) => sum + s.value, 0)
  const R = 60
  const C = 2 * Math.PI * R
  let offset = 0
  return (
    <svg viewBox="0 0 160 160" className="w-40 h-40 shrink-0" role="img" aria-label="Outstanding work units by queue state">
      <circle cx="80" cy="80" r={R} fill="none" stroke="#1f2937" strokeWidth="14" />
      {total > 0 && segments.filter((s) => s.value > 0).map((s) => {
        const length = (s.value / total) * C
        // A 2px gap between fills, per the mark spec, so adjacent segments read
        // as separate rather than as one continuous band.
        const dash = `${Math.max(length - 2, 0.5)} ${C - Math.max(length - 2, 0.5)}`
        const dashOffset = -offset
        offset += length
        return (
          <circle
            key={s.key}
            cx="80" cy="80" r={R} fill="none"
            stroke={STATE_COLORS[s.key]}
            strokeWidth={selected === s.key ? 18 : 14}
            strokeDasharray={dash}
            strokeDashoffset={dashOffset}
            transform="rotate(-90 80 80)"
            className="cursor-pointer"
            onClick={() => onSelect(selected === s.key ? null : s.key)}
          >
            <title>{`${STATE_LABELS[s.key]}: ${s.value.toLocaleString()} work units`}</title>
          </circle>
        )
      })}
      <text x="80" y="76" textAnchor="middle" className="fill-gray-100" style={{ fontSize: 22 }}>{centreValue}</text>
      <text x="80" y="94" textAnchor="middle" className="fill-gray-500" style={{ fontSize: 10 }}>{centreLabel}</text>
    </svg>
  )
}

// With a state selected the bar, the number and the ordering all describe that
// state. Otherwise filtering by In progress surfaced crawlers rendered as a
// bare "0" with a zero-width bar, sorted by counts that had nothing to do with
// why they matched.
function CrawlerBars({ crawlers, selectedState, selectedId, onSelect }: {
  crawlers: QueueCrawlerSummary[]
  selectedState: StateKey | null
  selectedId: number | null
  onSelect: (id: number) => void
}) {
  const primary = (c: QueueCrawlerSummary) =>
    selectedState ? unitsInState(c, selectedState) : c.claimable_units
  const secondary = (c: QueueCrawlerSummary) => (selectedState ? 0 : c.held_units)
  const max = Math.max(1, ...crawlers.map((c) => primary(c) + secondary(c)))
  if (crawlers.length === 0) {
    return <div className="text-sm text-gray-600 italic px-1 py-4">No crawler has work in this state.</div>
  }
  return (
    <div className="flex flex-col gap-0.5">
      {crawlers.map((c) => {
        const isSelected = c.crawler_id === selectedId
        return (
          <button
            key={c.crawler_id}
            onClick={() => onSelect(c.crawler_id)}
            aria-pressed={isSelected}
            className={`grid grid-cols-[11rem_1fr_5rem] items-center gap-3 px-2 py-1 rounded text-left transition-colors ${
              isSelected ? 'bg-gray-800' : 'hover:bg-gray-900'
            }`}
          >
            <span className={`text-sm truncate ${isSelected ? 'text-white' : 'text-gray-400'}`}>{c.site_name}</span>
            <span className="flex items-center h-2.5">
              {/* Emphasis, not a hue per crawler: past a handful of series a
                  per-crawler color carries no information a reader can use. */}
              <span
                className="h-2.5 rounded-r-sm"
                style={{
                  width: `${(primary(c) / max) * 100}%`,
                  background: isSelected
                    ? STATE_COLORS[selectedState ?? 'claimable']
                    : '#4b5563',
                }}
              />
              {secondary(c) > 0 && (
                <span
                  className="h-2.5 rounded-r-sm ml-0.5"
                  style={{ width: `${(secondary(c) / max) * 100}%`, background: STATE_COLORS.held }}
                />
              )}
            </span>
            <span className="text-sm text-gray-400 text-right tabular-nums">
              {primary(c).toLocaleString()}
              {secondary(c) > 0 && <span className="text-gray-600"> +{secondary(c).toLocaleString()}</span>}
            </span>
          </button>
        )
      })}
    </div>
  )
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 min-w-0">
      <div className="text-xs uppercase tracking-wide text-gray-500 mb-3">{title}</div>
      {children}
    </div>
  )
}

function Field({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1">
      <span className="text-sm text-gray-500">{label}</span>
      <span className="text-sm text-gray-200 text-right tabular-nums">
        {value}
        {hint && <span className="block text-xs text-gray-400">{hint}</span>}
      </span>
    </div>
  )
}

function CrawlerDetail({ crawler, window, next, nextLoading, nextError }: {
  crawler: QueueCrawlerSummary
  window: number
  next: QueueNextItem[]
  nextLoading: boolean
  nextError: string | null
}) {
  const units = crawler.release_units + crawler.stock_units
  const releasePct = units > 0 ? (crawler.release_units / units) * 100 : 0
  // With no pending units at all, a 0% release segment left the stock segment
  // filling the whole track -- a full stock-composition bar beside "0 release,
  // 0 stock item" for an idle crawler, or one reached via the In progress
  // filter. Nothing to compose means no bar.
  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <Panel title="Age & composition">
        <Field label="Oldest wait" value={formatDuration(crawler.oldest_wait_seconds)} />
        <Field label="Under 1h" value={crawler.age_buckets.under_1h.toLocaleString()} />
        <Field label="1–24h" value={crawler.age_buckets.under_24h.toLocaleString()} />
        <Field label="Over 24h" value={crawler.age_buckets.over_24h.toLocaleString()} />
        <div className="mt-3">
          {units > 0 && (
            <div className="flex h-2.5 gap-0.5">
              <span className="rounded-l-sm" style={{ width: `${releasePct}%`, background: STATE_COLORS.claimable }} />
              <span className="rounded-r-sm" style={{ width: `${100 - releasePct}%`, background: '#6b7280' }} />
            </div>
          )}
          <div className="flex justify-between text-xs text-gray-400 mt-1.5">
            <span>{crawler.release_units.toLocaleString()} release</span>
            <span>{crawler.stock_units.toLocaleString()} stock item</span>
          </div>
          {crawler.requires_discogs_release && (
            <div className="text-xs text-gray-400 mt-2">
              Requires a Discogs release, so stock-item targets never reach it.
            </div>
          )}
        </div>
      </Panel>

      <Panel title="Throughput & ETA">
        <Field
          label={`Listing rows touched / ${formatDuration(window)}`}
          value={crawler.results_last_hour.toLocaleString()}
        />
        <Field label="Last listing touched" value={formatDuration(crawler.last_result_seconds_ago)} />
        <Field label="Est. drain" value={formatDuration(crawler.eta_seconds)} />
        <div className="text-xs text-gray-400 mt-3 leading-relaxed">
          Distinct listing rows whose last-checked stamp moved — not a count of
          searches. A release crawl that finds nothing still counts, because it
          clears the price on an existing row; a first-ever miss writes no row
          at all and does not, and neither does a stock-item miss. Repeat passes
          over the same listing inside the window count once. The estimate
          divides this crawler's position in the claim order by the queue's
          recent drain rate.
        </div>
      </Panel>

      <Panel title="Next up">
        {nextLoading ? (
          <div className="text-sm text-gray-600 italic">Loading…</div>
        ) : nextError ? (
          <div className="text-sm text-red-400">{nextError}</div>
        ) : next.length === 0 ? (
          <div className="text-sm text-gray-600 italic">Nothing claimable for this crawler.</div>
        ) : (
          <div className="flex flex-col gap-1 max-h-64 overflow-y-auto">
            {next.map((item, i) => (
              <div key={i} className="flex items-baseline justify-between gap-3 text-sm">
                <span className="truncate text-gray-300">
                  {item.artist || '—'} <span className="text-gray-600">–</span> {item.title || '—'}
                </span>
                <span className="shrink-0 text-xs text-gray-400 tabular-nums">
                  {item.kind === 'stock' ? 'stock' : 'release'} · {formatDuration(item.waiting_seconds)}
                  {item.narrowed && <span title="Narrowed by an earlier pass"> · ↩</span>}
                </span>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </div>
  )
}

export default function QueueView() {
  const [summary, setSummary] = useState<QueueSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedState, setSelectedState] = useState<StateKey | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [next, setNext] = useState<QueueNextItem[]>([])
  const [nextLoading, setNextLoading] = useState(false)
  const [nextError, setNextError] = useState<string | null>(null)
  const generation = useRef(0)

  // Defence in depth. The scheduler below now guarantees one request at a time,
  // so no ordinary path can render a stale response -- but this is what makes
  // that a guarantee rather than an assumption, and it is what keeps the
  // property if load() ever gains a second caller.
  const load = useCallback(async () => {
    const mine = ++generation.current
    try {
      const next = await getQueueSummary()
      if (mine !== generation.current) return
      setSummary(next)
      setError(null)
    } catch (e: any) {
      if (mine !== generation.current) return
      setError(e?.message || 'Could not load the queue')
    }
  }, [])

  // Polled, not streamed: the SSE stream carries no queue-depth event, and
  // adding one would mean touching a fan-out whose per-user filtering rules are
  // load-bearing. Paused while the tab isn't the active view or the document is
  // hidden, so a backgrounded tab isn't querying the queue every 10 seconds.
  //
  // Self-scheduling rather than setInterval, so at most one summary is ever in
  // flight. The generation counter above only stops a stale response being
  // rendered; it does nothing about a slow one still holding a connection. A
  // summary is a REPEATABLE READ transaction on the same app pool the crawl
  // workers claim through, so an interval firing faster than the query settles
  // would stack transactions on that pool and compete with the workers --
  // exactly the harm this tab exists to detect, caused by the tab itself.
  useEffect(() => {
    let disposed = false
    let handle: ReturnType<typeof setTimeout> | null = null
    let active = false
    // Bumped on every stop, so an in-flight request whose continuation lands
    // after a hide/show cannot leave a second loop running alongside the new one.
    let run = 0
    // The token alone is not enough. stop() can invalidate a loop but cannot
    // recall the HTTP request it already issued, so a hide/show while one is
    // pending would have start() launch a second alongside it -- and a user
    // alt-tabbing during a slow query could stack several, which is the very
    // thing the self-scheduling exists to prevent. start() therefore defers to
    // an in-flight request, and that request resumes the loop when it settles.
    let inFlight = false

    function schedule(token: number) {
      handle = setTimeout(() => tick(token), POLL_MS)
    }

    async function tick(token: number) {
      if (disposed || token !== run) return
      inFlight = true
      try {
        await load()
      } finally {
        inFlight = false
      }
      if (disposed) return
      // Superseded by a stop while in flight: hand the loop back to whatever
      // the current run is, rather than leaving it dead or doubling it.
      if (token !== run) {
        if (active) schedule(run)
        return
      }
      schedule(token)
    }
    function start() {
      if (active) return
      active = true
      if (inFlight) return
      tick(++run)
    }
    function stop() {
      active = false
      run++
      if (handle) { clearTimeout(handle); handle = null }
    }
    function onVisibility() {
      if (document.visibilityState === 'visible') start(); else stop()
    }
    onVisibility()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      disposed = true
      document.removeEventListener('visibilitychange', onVisibility)
      stop()
    }
  }, [load])

  useEffect(() => {
    if (selectedId === null) { setNext([]); setNextError(null); return }
    let cancelled = false
    setNextLoading(true)
    getQueueNext(selectedId)
      .then((items) => { if (!cancelled) { setNext(items); setNextError(null) } })
      // Not folded into an empty list: in a diagnostic view "nothing claimable
      // for this crawler" is a finding, and a failed request must never be
      // mistaken for one.
      .catch((e: any) => { if (!cancelled) { setNext([]); setNextError(e?.message || 'Could not load') } })
      .finally(() => { if (!cancelled) setNextLoading(false) })
    return () => { cancelled = true }
  }, [selectedId, summary?.generated_at])

  const visibleCrawlers = useMemo(() => {
    const all = summary?.crawlers ?? []
    // Every state the donut offers filters for real. Leaving one as a no-op
    // that still reads aria-pressed presents a filter that isn't one.
    const filtered = selectedState === null
      ? all
      : all.filter((c) => unitsInState(c, selectedState) > 0)
    const weight = (c: QueueCrawlerSummary) =>
      selectedState ? unitsInState(c, selectedState) : c.claimable_units + c.held_units
    return [...filtered].sort((a, b) => weight(b) - weight(a))
  }, [summary, selectedState])

  const selected = summary?.crawlers.find((c) => c.crawler_id === selectedId) ?? null

  if (error && !summary) {
    return <div className="p-6 text-sm text-red-400">{error}</div>
  }
  if (!summary) {
    return <div className="p-6 text-sm text-gray-500">Loading queue…</div>
  }

  const t = summary.totals
  // Unactionable rows are pending rows too. Leaving them out made the centre
  // read "0 rows" while the Unactionable tile showed a non-zero count -- the
  // ring claiming an empty queue next to a tile saying otherwise. They
  // contribute no arc, by definition (no enabled crawler resolves for them),
  // which is exactly why the centre counts rows and the segments count units.
  const rows = t.claimable_rows + t.held_rows + t.in_progress_rows + t.unactionable_rows

  return (
    <div className="h-full overflow-y-auto p-6 flex flex-col gap-6 text-left">
      {/* A poll that fails after the first success would otherwise leave the
          last good snapshot on screen looking live, which in an operational
          view is worse than showing nothing. */}
      {error && (
        <div className="text-sm text-red-400 border border-red-900 rounded-lg px-4 py-2">
          Showing a stale snapshot — the last refresh failed: {error}
        </div>
      )}
      <div className="flex flex-wrap gap-3">
        {/* Every other tile is global database state. This one is the flag of
            whichever Machine served the poll, and on a multi-Machine
            deployment consecutive polls can land on different ones -- so it
            says whose pool it is rather than implying deployment-wide health.
            Nothing durable records another Machine's pool state. */}
        <StatTile
          label="Worker pool"
          value={summary.pool_running ? 'Running' : 'Stopped'}
          hint="this machine only"
          accent={summary.pool_running ? undefined : STATUS_CRITICAL}
        />
        <StatTile label="Claimable" value={t.claimable_rows.toLocaleString()} hint="rows" />
        <StatTile label="In progress" value={t.in_progress_rows.toLocaleString()} hint="rows" />
        <StatTile label="Held" value={t.held_rows.toLocaleString()} hint="waiting on a cooldown" />
        <StatTile
          label="Stranded"
          value={t.stranded_rows.toLocaleString()}
          hint={`claimed over ${formatDuration(summary.stranded_after_seconds)} ago`}
          accent={t.stranded_rows > 0 ? STATUS_CRITICAL : undefined}
        />
        <StatTile
          label="Unactionable"
          value={t.unactionable_rows.toLocaleString()}
          hint="no enabled crawler"
          accent={t.unactionable_rows > 0 ? STATUS_WARNING : undefined}
        />
        <StatTile
          label="Drain rate"
          value={t.rows_done_last_hour.toLocaleString()}
          hint={`rows / ${formatDuration(summary.activity_window_seconds)}`}
        />
        <StatTile label="Queue ETA" value={formatDuration(t.eta_seconds)} hint="at that rate" />
      </div>

      <div className="flex flex-col lg:flex-row gap-6 items-start">
        <div className="flex items-center gap-4">
          <StateDonut
            segments={[
              { key: 'in_progress', value: t.in_progress_units },
              { key: 'claimable', value: t.claimable_units },
              { key: 'held', value: t.held_units },
            ]}
            centreValue={rows.toLocaleString()}
            centreLabel="rows"
            selected={selectedState}
            onSelect={setSelectedState}
          />
          <div className="flex flex-col gap-2">
            {(['in_progress', 'claimable', 'held'] as StateKey[]).map((key) => {
              const value = key === 'in_progress' ? t.in_progress_units
                : key === 'claimable' ? t.claimable_units : t.held_units
              return (
                <button
                  key={key}
                  onClick={() => setSelectedState(selectedState === key ? null : key)}
                  aria-pressed={selectedState === key}
                  className={`flex items-center gap-2 text-sm px-2 py-1 rounded transition-colors ${
                    selectedState === key ? 'bg-gray-800 text-white' : 'text-gray-400 hover:bg-gray-900'
                  }`}
                >
                  <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: STATE_COLORS[key] }} />
                  {STATE_LABELS[key]}
                  <span className="text-gray-500 tabular-nums">{value.toLocaleString()}</span>
                </button>
              )
            })}
            <div className="text-xs text-gray-400 max-w-52 leading-relaxed mt-1">
              Work units — one search per (target, crawler) pair. A queue row
              names a target, not a crawler, so units far outnumber rows. In
              progress counts every unit of a claimed row, including ones that
              row has already finished.
            </div>
          </div>
        </div>

        <div className="flex-1 min-w-0 w-full">
          <div className="flex items-baseline justify-between mb-2">
            <div className="text-xs uppercase tracking-wide text-gray-500">
              Work units by crawler
            </div>
            {selectedState && (
              <button onClick={() => setSelectedState(null)} className="text-xs text-gray-500 hover:text-gray-300">
                Clear {STATE_LABELS[selectedState].toLowerCase()} filter
              </button>
            )}
          </div>
          <CrawlerBars
            crawlers={visibleCrawlers}
            selectedState={selectedState}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        </div>
      </div>

      <div className="border-t border-gray-800 pt-6">
        {selected ? (
          <>
            <div className="text-sm text-gray-300 mb-3">{selected.site_name}</div>
            <CrawlerDetail
              crawler={selected}
              window={summary.activity_window_seconds}
              next={next}
              nextLoading={nextLoading}
              nextError={nextError}
            />
          </>
        ) : (
          <div className="text-sm text-gray-600 italic">
            Select a crawler above to see its backlog in detail.
          </div>
        )}
      </div>
    </div>
  )
}
