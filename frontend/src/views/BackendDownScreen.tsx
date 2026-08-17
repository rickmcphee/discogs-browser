export default function BackendDownScreen() {
  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-4 bg-gray-950/90 text-gray-300"
    >
      <div aria-hidden="true" className="w-8 h-8 border-2 border-white border-t-transparent rounded-full animate-spin" />
      <p className="text-sm">Can't reach the server. Retrying…</p>
    </div>
  )
}
