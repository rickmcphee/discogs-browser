import { useState } from 'react'
import { redeemInvite } from '../api/client'
import { primaryButtonClass } from '../styles/buttons'
import { textInputClass } from '../styles/inputs'

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
    <div className="min-h-dvh flex items-center justify-center bg-gray-950 p-4">
      <form onSubmit={submit} className="bg-gray-900 border border-gray-700 rounded-xl shadow-xl p-8 w-full max-w-xs space-y-4">
        <h1 className="text-base font-semibold text-white">Enter your invite code</h1>
        <input
          type="text" placeholder="Invite code" value={code}
          onChange={e => setCode(e.target.value)}
          className={`w-full px-4 py-2 ${textInputClass()}`}
          autoFocus
        />
        {error && <p className="text-red-400 text-sm">{error}</p>}
        <button type="submit" disabled={busy}
          className={`w-full py-2 text-sm disabled:opacity-50 ${primaryButtonClass()}`}>
          {busy ? 'Checking…' : 'Continue'}
        </button>
      </form>
    </div>
  )
}
