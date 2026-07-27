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
    <div className="min-h-screen flex items-center justify-center bg-gray-100">
      <form onSubmit={submit} className="bg-white p-8 rounded shadow w-80 space-y-4">
        <h1 className="text-xl font-semibold">Enter your invite code</h1>
        <input
          type="text" placeholder="Invite code" value={code}
          onChange={e => setCode(e.target.value)}
          className="w-full border rounded px-3 py-2" autoFocus
        />
        {error && <p className="text-red-600 text-sm">{error}</p>}
        <button type="submit" disabled={busy}
          className="w-full bg-blue-600 text-white rounded py-2 disabled:opacity-50">
          {busy ? 'Checking…' : 'Continue'}
        </button>
      </form>
    </div>
  )
}
