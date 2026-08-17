export default function BackendDownScreen() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center gap-4 bg-gray-950 text-gray-300">
      <div className="w-8 h-8 border-2 border-white border-t-transparent rounded-full animate-spin" />
      <p className="text-sm">Can't reach the server. Retrying…</p>
    </div>
  )
}
