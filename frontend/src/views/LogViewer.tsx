import { useEffect, useState, useMemo, memo } from 'react'
import { openLogsStream, screenshotUrl, clearLogs } from '../api/client'
import { textInputClass } from '../styles/inputs'

type Level = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'OTHER'

interface LogEntry {
  id: number
  time: string
  level: Level
  logger: string
  message: string
  machine: string
  screenshotPath?: string  // parsed from SCREENSHOT: marker
}

const SCREENSHOT_RE = /\s+SCREENSHOT:(\S+\.png)\s*$/
const URL_RE = /(https?:\/\/[^\s]+)/g
const BROWSABLE_URL_RE = /^https?:\/\/www\./
const KNOWN_LEVELS: Level[] = ['DEBUG', 'INFO', 'WARNING', 'ERROR']

function renderMessage(msg: string) {
  const parts = msg.split(URL_RE)
  return parts.map((part, i) =>
    URL_RE.test(part) && BROWSABLE_URL_RE.test(part)
      ? <a key={i} href={part} target="_blank" rel="noreferrer"
           className="text-gray-400 hover:text-white underline break-all">{part}</a>
      : part
  )
}

function parsePayload(data: any): LogEntry {
  let message: string = data.message ?? ''
  let screenshotPath: string | undefined
  const sm = message.match(SCREENSHOT_RE)
  if (sm) {
    screenshotPath = sm[1]
    message = message.slice(0, message.length - sm[0].length)
  }
  const level = (KNOWN_LEVELS as string[]).includes(data.level) ? (data.level as Level) : 'OTHER'
  return {
    id: data.id,
    time: data.time ?? '',
    level,
    logger: data.logger ?? '',
    message,
    machine: data.machine ?? '',
    screenshotPath,
  }
}

const LEVEL_COLORS: Record<Level, string> = {
  ERROR:   'text-red-400',
  WARNING: 'text-yellow-400',
  INFO:    'text-gray-300',
  DEBUG:   'text-gray-500',
  OTHER:   'text-gray-600',
}

const LEVEL_BG: Record<Level, string> = {
  ERROR:   'bg-red-900/30',
  WARNING: 'bg-yellow-900/20',
  INFO:    '',
  DEBUG:   '',
  OTHER:   '',
}

const ALL_LEVELS: Level[] = ['DEBUG', 'INFO', 'WARNING', 'ERROR']


function LogViewer() {
  const [entries, setEntries] = useState<LogEntry[]>([])
  const [paused, setPaused] = useState(false)
  const [levelFilter, setLevelFilter] = useState<Set<Level>>(new Set(['INFO', 'WARNING', 'ERROR']))
  const [msgFilter, setMsgFilter] = useState('')
  const [regexError, setRegexError] = useState(false)

  useEffect(() => {
    // Reconnect with the active levels so the server-side stream (history seed
    // + live tail) only carries the levels being viewed; a DEBUG burst can no
    // longer crowd INFO/WARNING/ERROR out of the buffer.
    setEntries([])
    const source = openLogsStream(Array.from(levelFilter))
    source.onmessage = (e) => {
      const data = JSON.parse(e.data)
      setEntries((prev) => {
        const entry = parsePayload(data)
        const next = [...prev, entry]
        return next.length > 2000 ? next.slice(-2000) : next
      })
    }
    source.onerror = () => source.close()
    return () => source.close()
  }, [levelFilter])

  const filtered = useMemo(() => {
    let re: RegExp | null = null
    setRegexError(false)
    if (msgFilter) {
      try { re = new RegExp(msgFilter, 'i') } catch { setRegexError(true) }
    }
    return entries.filter((e) => {
      if (e.level !== 'OTHER' && !levelFilter.has(e.level)) return false
      if (re && !re.test(e.message)) return false
      return true
    })
  }, [entries, levelFilter, msgFilter])

  function toggleLevel(level: Level) {
    setLevelFilter((prev) => {
      const next = new Set(prev)
      next.has(level) ? next.delete(level) : next.add(level)
      return next
    })
  }

  return (
    <div className="flex flex-col h-full bg-gray-950 text-xs font-mono text-left">
      {/* Toolbar */}
      <div className="flex items-center gap-3 px-4 py-2 border-b border-gray-800 bg-gray-900 flex-wrap">
        <div className="flex gap-1">
          {ALL_LEVELS.map((level) => (
            <button
              key={level}
              onClick={() => toggleLevel(level)}
              className={`px-2 py-0.5 rounded-full text-xs font-medium transition-colors ${
                levelFilter.has(level)
                  ? level === 'ERROR'   ? 'bg-red-700 text-white'
                  : level === 'WARNING' ? 'bg-yellow-700 text-white'
                  : level === 'INFO'    ? 'bg-gray-600 text-white'
                  :                      'bg-gray-700 text-gray-400'
                  : 'bg-gray-800 text-gray-600'
              }`}
            >
              {level}
            </button>
          ))}
        </div>

        <div className="relative flex-1 min-w-40">
          <input
            type="text"
            value={msgFilter}
            onChange={(e) => setMsgFilter(e.target.value)}
            placeholder="Filter message (regexp)…"
            className={`w-full px-3 py-0.5 pr-11 md:pr-7 ${textInputClass(regexError)}`}
          />
          <button
            onClick={() => setMsgFilter('')}
            aria-label="Clear message filter"
            className="absolute right-0 top-1/2 flex h-11 w-11 -translate-y-1/2 items-center justify-center text-gray-500 hover:text-gray-300 md:right-2.5 md:h-auto md:w-auto"
          >
            <span aria-hidden="true">✕</span>
          </button>
        </div>

        <div className="flex items-center gap-2 ml-auto">
          <span className="text-gray-600">{filtered.length} lines</span>
          <button
            onClick={() => { setEntries([]); clearLogs() }}
            className="text-gray-500 hover:text-gray-300 transition-colors"
          >
            Clear
          </button>
          <button
            onClick={() => setPaused((p) => !p)}
            className={`px-2 py-0.5 rounded-full transition-colors ${
              paused ? 'bg-yellow-600 text-white hover:bg-yellow-500' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
            }`}
          >
            {paused ? 'Resume' : 'Pause'}
          </button>
        </div>
      </div>

      {/* Header strip and rows share one horizontal scroller with one
          min-width, so the columns stay in step as it scrolls. Above the
          breakpoint the min-width resolves to 100% and nothing scrolls
          sideways at all. */}
      <div className="flex-1 min-h-0 overflow-x-auto overflow-y-hidden">
      <div className="flex h-full min-w-[52rem] flex-col md:min-w-full">
      {/* Column headers */}
      <div className="flex gap-0 px-4 py-1 border-b border-gray-800 text-gray-600 select-none">
        <span className="w-36 shrink-0">Time</span>
        <span className="w-16 shrink-0">Level</span>
        <span className="w-28 shrink-0">Logger</span>
        <span className="w-24 shrink-0">Machine</span>
        <span>Message</span>
      </div>

      {/* Log rows */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {filtered.length === 0 && (
          <div className="px-4 py-4 text-gray-600 italic">No log entries.</div>
        )}
        {[...filtered].reverse().map((e) => (
          <div
            key={e.id}
            className={`flex gap-0 px-4 py-0.5 hover:bg-gray-900 ${LEVEL_BG[e.level]}`}
          >
            <span className="w-36 shrink-0 text-gray-600">{e.time}</span>
            <span className={`w-16 shrink-0 font-semibold ${LEVEL_COLORS[e.level]}`}>{e.level}</span>
            <span className="w-28 shrink-0 text-gray-500 truncate">{e.logger}</span>
            <span className="w-24 shrink-0 text-gray-600 truncate">{e.machine}</span>
            <span className={`flex-1 break-all whitespace-pre-wrap text-left ${LEVEL_COLORS[e.level]}`}>
              {renderMessage(e.message)}
              {e.screenshotPath && (
                <a
                  href={screenshotUrl(e.screenshotPath)}
                  target="_blank"
                  rel="noreferrer"
                  className="ml-2 text-gray-400 hover:text-white transition-colors"
                  title="View screenshot"
                >
                  📷
                </a>
              )}
            </span>
          </div>
        ))}
      </div>
      </div>
      </div>

    </div>
  )
}

export default memo(LogViewer)
