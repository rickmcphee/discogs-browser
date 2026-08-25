import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import LogViewer from '../views/LogViewer'
import { openLogsStream } from '../api/client'

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
  openLogsStream: vi.fn(() => new MockEventSource()),
  screenshotUrl: (path: string) => `/api/screenshots/${path}`,
  clearLogs: vi.fn(),
}))

let nextId = 1
function emitEntry(overrides: Partial<{ time: string; level: string; logger: string; message: string; machine: string }>) {
  act(() => {
    MockEventSource.instance?.emit({
      id: nextId++,
      time: '2026-06-27 15:30:32',
      level: 'INFO',
      logger: 'main',
      machine: 'fdca1234',
      message: 'default message',
      ...overrides,
    })
  })
}

beforeEach(() => {
  MockEventSource.instance = null
  nextId = 1
  ;(openLogsStream as any).mockClear()
})
afterEach(() => { vi.restoreAllMocks() })

describe('LogViewer', () => {
  it('renders with empty state initially', () => {
    render(<LogViewer />)
    expect(screen.getByText(/No log entries/i)).toBeInTheDocument()
  })

  it('displays a structured INFO log entry, including its machine tag', () => {
    render(<LogViewer />)
    emitEntry({ level: 'INFO', logger: 'main', machine: 'fdca1234', message: 'Discogs Browser started' })
    expect(screen.getByText('Discogs Browser started')).toBeInTheDocument()
    expect(screen.getAllByText('INFO').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('main')).toBeInTheDocument()
    expect(screen.getByText('fdca1234')).toBeInTheDocument()
  })

  it('displays an ERROR entry', () => {
    render(<LogViewer />)
    emitEntry({ level: 'ERROR', logger: 'routers.crawl', message: 'Something broke' })
    expect(screen.getByText('Something broke')).toBeInTheDocument()
    expect(screen.getAllByText('ERROR').length).toBeGreaterThanOrEqual(2)
  })

  it('hides INFO lines when INFO toggle is off', () => {
    render(<LogViewer />)
    emitEntry({ level: 'INFO', message: 'Hello world' })
    expect(screen.getByText('Hello world')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'INFO' }))
    expect(screen.queryByText('Hello world')).not.toBeInTheDocument()
  })

  it('filters by message regexp', () => {
    render(<LogViewer />)
    emitEntry({ message: 'Collection refresh started' })
    emitEntry({ message: 'Crawler loaded successfully' })

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
    emitEntry({ message: 'Something happened' })
    expect(screen.getByText('Something happened')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(screen.queryByText('Something happened')).not.toBeInTheDocument()
    expect(screen.getByText(/No log entries/i)).toBeInTheDocument()
  })

  it('shows line count', () => {
    render(<LogViewer />)
    emitEntry({ message: 'Line one' })
    emitEntry({ message: 'Line two' })
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
    emitEntry({ level: 'DEBUG', message: 'debug detail' })
    expect(screen.queryByText('debug detail')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'DEBUG' }))
    emitEntry({ level: 'DEBUG', message: 'debug after enable' })
    expect(screen.getByText('debug after enable')).toBeInTheDocument()
  })

  it('opens the stream with the default visible levels (DEBUG excluded)', () => {
    render(<LogViewer />)
    const levels = (openLogsStream as any).mock.calls.at(-1)[0]
    expect(new Set(levels)).toEqual(new Set(['INFO', 'WARNING', 'ERROR']))
  })

  it('reconnects with the updated levels when a toggle changes', async () => {
    render(<LogViewer />)
    fireEvent.click(screen.getByRole('button', { name: 'DEBUG' }))
    await waitFor(() => {
      const levels = (openLogsStream as any).mock.calls.at(-1)[0]
      expect(new Set(levels)).toEqual(new Set(['DEBUG', 'INFO', 'WARNING', 'ERROR']))
    })
  })

  it('renders a multi-line message with its line breaks intact', () => {
    render(<LogViewer />)
    emitEntry({ level: 'ERROR', message: 'caught something\nTraceback (most recent call last):\n  File "x.py", line 1' })
    const cell = screen.getByText(/caught something/)
    expect(cell.className).toContain('whitespace-pre-wrap')
  })
})
