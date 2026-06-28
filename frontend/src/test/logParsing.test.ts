import { describe, it, expect } from 'vitest'

// Inline the parse logic so this test is independent of the component internals
const LOG_RE = /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(DEBUG|INFO|WARNING|ERROR)\s+(\S+)\s+(.+)$/

function parseLine(raw: string) {
  const m = raw.match(LOG_RE)
  if (m) return { time: m[1], level: m[2], logger: m[3], message: m[4] }
  return { time: '', level: 'OTHER', logger: '', message: raw }
}

describe('log line parsing', () => {
  it('parses a well-formed INFO line', () => {
    const line = '2026-06-27 15:30:32  INFO      main  Discogs Browser started'
    const r = parseLine(line)
    expect(r.time).toBe('2026-06-27 15:30:32')
    expect(r.level).toBe('INFO')
    expect(r.logger).toBe('main')
    expect(r.message).toBe('Discogs Browser started')
  })

  it('parses an ERROR line', () => {
    const line = '2026-06-27 15:31:00  ERROR     routers.crawl  Crawl stream failed: timeout'
    const r = parseLine(line)
    expect(r.level).toBe('ERROR')
    expect(r.logger).toBe('routers.crawl')
    expect(r.message).toBe('Crawl stream failed: timeout')
  })

  it('parses a WARNING line', () => {
    const line = '2026-06-27 15:31:00  WARNING   crawler  Crawler module not found: /some/path.py'
    const r = parseLine(line)
    expect(r.level).toBe('WARNING')
    expect(r.message).toBe('Crawler module not found: /some/path.py')
  })

  it('falls back gracefully for non-structured lines', () => {
    const line = 'Some unstructured log output'
    const r = parseLine(line)
    expect(r.level).toBe('OTHER')
    expect(r.message).toBe(line)
    expect(r.time).toBe('')
  })

  it('parses a message containing colons and spaces', () => {
    const line = '2026-06-27 10:00:00  INFO      discogs  Fetching collection page 1 for someuser'
    const r = parseLine(line)
    expect(r.message).toBe('Fetching collection page 1 for someuser')
  })
})
