import { useState } from 'react'
import { redeemInvite } from '../api/client'

export default function InviteCodeScreen({
  signupToken,
  onRedeemed,
}: {
  signupToken: string
  onRedeemed: () => void
}) {
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await redeemInvite(signupToken, code)
      onRedeemed()
    } catch (err: any) {
      setError(err.message || 'Invalid or already-used invite code')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-950">
      <form onSubmit={submit} className="bg-gray-900 border border-gray-700 rounded-lg shadow-xl p-8 w-80 space-y-4">
        <h1 className="text-lg font-semibold text-white">Enter your invite code</h1>
        <input
          type="text" placeholder="Invite code" value={code}
          onChange={e => setCode(e.target.value)}
          className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-white placeholder-gray-500 focus:outline-none focus:border-indigo-500"
          autoFocus
        />
        {error && <p className="text-red-400 text-sm">{error}</p>}
        <button type="submit" disabled={busy}
          className="w-full bg-indigo-600 hover:bg-indigo-500 text-white rounded py-2 text-sm font-medium transition-colors disabled:opacity-50">
          {busy ? 'Checking…' : 'Continue'}
        </button>
      </form>
    </div>
  )
}
