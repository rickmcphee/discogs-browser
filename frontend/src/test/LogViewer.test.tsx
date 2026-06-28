import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import LogViewer from '../views/LogViewer'

// Mock the EventSource used by openLogsStream
class MockEventSource {
  static instance: MockEventSource | null = null
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()

  constructor() {
    MockEventSource.instance = this
  }

  emit(data: object) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent)
  }
}

vi.mock('../api/client', () => ({
  openLogsStream: () => new MockEventSource(),
}))

function emitLine(line: string) {
  act(() => { MockEventSource.instance?.emit({ line }) })
}

beforeEach(() => { MockEventSource.instance = null })
afterEach(() => { vi.restoreAllMocks() })

describe('LogViewer', () => {
  it('renders with empty state initially', () => {
    render(<LogViewer />)
    expect(screen.getByText(/No log entries/i)).toBeInTheDocument()
  })

  it('displays a parsed INFO log line', () => {
    render(<LogViewer />)
    emitLine('2026-06-27 15:30:32  INFO      main  Discogs Browser started')
    expect(screen.getByText('Discogs Browser started')).toBeInTheDocument()
    // INFO appears in both the toggle button and the log row — at least 2
    expect(screen.getAllByText('INFO').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('main')).toBeInTheDocument()
  })

  it('displays an ERROR line', () => {
    render(<LogViewer />)
    emitLine('2026-06-27 15:31:00  ERROR     routers.crawl  Something broke')
    expect(screen.getByText('Something broke')).toBeInTheDocument()
    // ERROR appears in both the toggle button and the log row
    expect(screen.getAllByText('ERROR').length).toBeGreaterThanOrEqual(2)
  })

  it('hides INFO lines when INFO toggle is off', () => {
    render(<LogViewer />)
    emitLine('2026-06-27 10:00:00  INFO      main  Hello world')
    expect(screen.getByText('Hello world')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'INFO' }))
    expect(screen.queryByText('Hello world')).not.toBeInTheDocument()
  })

  it('filters by message regexp', () => {
    render(<LogViewer />)
    emitLine('2026-06-27 10:00:00  INFO      main  Collection refresh started')
    emitLine('2026-06-27 10:00:01  INFO      main  Crawler loaded successfully')

    const input = screen.getByPlaceholderText(/Filter message/i)
    fireEvent.change(input, { target: { value: 'refresh' } })

    expect(screen.getByText('Collection refresh started')).toBeInTheDocument()
    expect(screen.queryByText('Crawler loaded successfully')).not.toBeInTheDocument()
  })

  it('shows a regex error indicator for invalid regexp', () => {
    render(<LogViewer />)
    const input = screen.getByPlaceholderText(/Filter message/i)
    fireEvent.change(input, { target: { value: '[invalid' } })
    expect(input).toHaveClass('border-red-500')
  })

  it('clears all entries when Clear is clicked', () => {
    render(<LogViewer />)
    emitLine('2026-06-27 10:00:00  INFO      main  Something happened')
    expect(screen.getByText('Something happened')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(screen.queryByText('Something happened')).not.toBeInTheDocument()
    expect(screen.getByText(/No log entries/i)).toBeInTheDocument()
  })

  it('shows line count', () => {
    render(<LogViewer />)
    emitLine('2026-06-27 10:00:00  INFO      main  Line one')
    emitLine('2026-06-27 10:00:01  INFO      main  Line two')
    expect(screen.getByText('2 lines')).toBeInTheDocument()
  })

  it('closes EventSource on unmount', () => {
    const { unmount } = render(<LogViewer />)
    const source = MockEventSource.instance!
    unmount()
    expect(source.close).toHaveBeenCalled()
  })

  it('shows DEBUG lines only when DEBUG toggle is enabled', () => {
    render(<LogViewer />)
    emitLine('2026-06-27 10:00:00  DEBUG     main  debug detail')

    // DEBUG is off by default
    expect(screen.queryByText('debug detail')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'DEBUG' }))
    expect(screen.getByText('debug detail')).toBeInTheDocument()
  })
})
