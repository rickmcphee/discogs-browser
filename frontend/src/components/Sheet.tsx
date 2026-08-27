import { useEffect, type ReactNode } from 'react'

interface Props {
  open: boolean
  onClose: () => void
  /** Accessible name for the dialog — there is no visible heading in every use. */
  label: string
  children: ReactNode
}

// A bottom sheet, the mobile stand-in for controls that live in a sidebar or a
// header row on desktop. Anchored to the bottom edge because that is where a
// thumb is, capped at three quarters of the viewport so the content behind it
// stays visible as context.
export default function Sheet({ open, onClose, label, children }: Props) {
  useEffect(() => {
    if (!open) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex flex-col justify-end">
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-black/60"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={label}
        className="relative max-h-[75dvh] overflow-y-auto rounded-t-2xl border-t border-gray-700 bg-gray-900 pb-safe px-safe"
      >
        {/* Grab handle. Purely a signal that the panel is dismissable by
            dragging it away on platforms whose users expect that; the sheet
            itself closes on the backdrop or Escape. */}
        <div aria-hidden="true" className="sticky top-0 flex justify-center bg-gray-900 pt-2 pb-1">
          <span className="h-1 w-10 rounded-full bg-gray-700" />
        </div>
        {children}
      </div>
    </div>
  )
}
