import { useEffect, useRef, type ReactNode } from 'react'

interface Props {
  open: boolean
  onClose: () => void
  /** Accessible name for the dialog — there is no visible heading in every use. */
  label: string
  children: ReactNode
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

// A bottom sheet, the mobile stand-in for controls that live in a sidebar or a
// header row on desktop. Anchored to the bottom edge because that is where a
// thumb is, capped at three quarters of the viewport so the content behind it
// stays visible as context.
export default function Sheet({ open, onClose, label, children }: Props) {
  const panelRef = useRef<HTMLDivElement>(null)
  const restoreFocusRef = useRef<HTMLElement | null>(null)

  // `aria-modal` promises interaction is confined to the dialog, so the focus
  // has to actually be confined: moved in on open, cycled within the panel on
  // Tab, and handed back to whatever opened the sheet on close. Without this
  // the promise is a lie a screen reader acts on -- it stops announcing the
  // app behind the sheet while Tab walks straight into it.
  useEffect(() => {
    if (!open) return
    restoreFocusRef.current = document.activeElement as HTMLElement | null
    panelRef.current?.focus()
    return () => restoreFocusRef.current?.focus?.()
  }, [open])

  useEffect(() => {
    if (!open) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        onClose()
        return
      }
      if (e.key !== 'Tab') return
      const panel = panelRef.current
      if (!panel) return
      const focusable = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE))
      // The panel is its own fallback stop, so an empty sheet still traps.
      const first = focusable[0] ?? panel
      const last = focusable[focusable.length - 1] ?? panel
      const active = document.activeElement
      if (!panel.contains(active)) {
        e.preventDefault()
        first.focus()
      } else if (e.shiftKey && (active === first || active === panel)) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && active === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex flex-col justify-end">
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        className="relative z-10 max-h-[75dvh] overflow-y-auto rounded-t-2xl border-t border-gray-700 bg-gray-900 pb-safe px-safe focus:outline-none"
      >
        {/* The backdrop is pointer-only and the grab handle is decoration --
            it implements no dragging -- so without this button the only way
            out is Escape, which a switch or voice-control user may not have.
            The source sheet makes that a trap rather than an inconvenience:
            it is a multi-select, so toggling an option deliberately leaves it
            open. */}
        <div className="sticky top-0 flex items-center bg-gray-900 pt-2 pb-1">
          <span aria-hidden="true" className="mx-auto h-1 w-10 translate-x-5 rounded-full bg-gray-700" />
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex h-11 w-11 shrink-0 items-center justify-center text-gray-400 hover:text-white"
          >
            <span aria-hidden="true">✕</span>
          </button>
        </div>
        {children}
      </div>
      {/* Pointer-only dismiss: hidden from assistive tech and untabbable, so
          the trap above has nothing to reach around. Escape is the keyboard
          and screen-reader route out. */}
      <button
        type="button"
        aria-hidden="true"
        tabIndex={-1}
        onClick={onClose}
        className="absolute inset-0 z-0 bg-black/60"
      />
    </div>
  )
}
