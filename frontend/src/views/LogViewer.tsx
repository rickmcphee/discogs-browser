import { useEffect, useRef, useState, useMemo } from 'react'
import { openLogsStream, screenshotUrl, clearLogs } from '../api/client'

type Level = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'OTHER'

interface LogEntry {
  id: number
  time: string
  level: Level
  logger: string
  message: string
  raw: string
  screenshotPath?: string  // parsed from SCREENSHOT: marker
}

const LOG_RE = /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(DEBUG|INFO|WARNING|ERROR)\s+(\S+)\s+(.+)$/
const SCREENSHOT_RE = /\s+SCREENSHOT:(\S+\.png)\s*$/
const URL_RE = /(https?:\/\/[^\s]+)/g
const BROWSABLE_URL_RE = /^https?:\/\/www\./

function renderMessage(msg: string) {
  const parts = msg.split(URL_RE)
  return parts.map((part, i) =>
    URL_RE.test(part) && BROWSABLE_URL_RE.test(part)
      ? <a key={i} href={part} target="_blank" rel="noreferrer"
           className="text-indigo-400 hover:text-indigo-300 underline break-all">{part}</a>
      : part
  )
}

function parseLine(raw: string, id: number): LogEntry {
  const m = raw.match(LOG_RE)
  if (m) {
    let message = m[4]
    let screenshotPath: string | undefined
    const sm = message.match(SCREENSHOT_RE)
    if (sm) {
      screenshotPath = sm[1]
      message = message.slice(0, message.length - sm[0].length)
    }
    return { id, time: m[1], level: m[2] as Level, logger: m[3], message, screenshotPath, raw }
  }
  return { id, time: '', level: 'OTHER', logger: '', message: raw, raw }
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


export default function LogViewer() {
  const [entries, setEntries] = useState<LogEntry[]>([])
  const [paused, setPaused] = useState(false)
  const [levelFilter, setLevelFilter] = useState<Set<Level>>(new Set(['INFO', 'WARNING', 'ERROR']))
  const [msgFilter, setMsgFilter] = useState('')
  const [regexError, setRegexError] = useState(false)
  const idRef = useRef(0)

  useEffect(() => {
    const source = openLogsStream()
    source.onmessage = (e) => {
      const { line } = JSON.parse(e.data)
      if (!line) return
      setEntries((prev) => {
        const entry = parseLine(line, idRef.current++)
        const next = [...prev, entry]
        return next.length > 2000 ? next.slice(-2000) : next
      })
    }
    source.onerror = () => source.close()
    return () => source.close()
  }, [])

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
              className={`px-2 py-0.5 rounded text-xs font-medium transition-colors ${
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
            className={`w-full bg-gray-800 border rounded px-2 py-0.5 pr-6 text-gray-200 placeholder-gray-600 outline-none focus:border-indigo-500 ${
              regexError ? 'border-red-500' : 'border-gray-700'
            }`}
          />
          <button
            onClick={() => setMsgFilter('')}
            className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
          >
            ✕
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
            className={`px-2 py-0.5 rounded transition-colors ${
              paused ? 'bg-yellow-600 text-white hover:bg-yellow-500' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
            }`}
          >
            {paused ? 'Resume' : 'Pause'}
          </button>
        </div>
      </div>

      {/* Column headers */}
      <div className="flex gap-0 px-4 py-1 border-b border-gray-800 text-gray-600 select-none">
        <span className="w-36 shrink-0">Time</span>
        <span className="w-16 shrink-0">Level</span>
        <span className="w-28 shrink-0">Logger</span>
        <span>Message</span>
      </div>

      {/* Log rows */}
      <div className="flex-1 overflow-y-auto">
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
            <span className={`flex-1 break-all text-left ${LEVEL_COLORS[e.level]}`}>
              {renderMessage(e.message)}
              {e.screenshotPath && (
                <a
                  href={screenshotUrl(e.screenshotPath)}
                  target="_blank"
                  rel="noreferrer"
                  className="ml-2 text-indigo-400 hover:text-indigo-300 transition-colors"
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
  )
}
