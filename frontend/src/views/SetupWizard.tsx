import { useState } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { setupOwner, verifySetup } from '../api/client'

type Step = 'credentials' | 'totp' | 'recovery'

export default function SetupWizard({ onComplete }: { onComplete: () => void }) {
  const [step, setStep] = useState<Step>('credentials')
  const [bootstrapToken, setBootstrapToken] = useState('')
  const [password, setPassword] = useState('')
  const [uri, setUri] = useState('')
  const [code, setCode] = useState('')
  const [recovery, setRecovery] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  async function submitCredentials(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      const res = await setupOwner(bootstrapToken, password)
      setUri(res.provisioning_uri)
      setStep('totp')
    } catch {
      setError('Setup failed — check the bootstrap token.')
    }
  }

  async function submitTotp(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      const codes = await verifySetup(code)
      setRecovery(codes)
      setStep('recovery')
    } catch {
      setError('Invalid code — try again.')
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-100">
      <div className="bg-white p-8 rounded shadow w-96 space-y-4">
        <h1 className="text-xl font-semibold">Sign Up</h1>

        {step === 'credentials' && (
          <form onSubmit={submitCredentials} className="space-y-4">
            <p className="text-sm text-gray-600">
              Enter the bootstrap token from the server log and choose a password.
            </p>
            <input type="text" placeholder="Bootstrap token" value={bootstrapToken}
              onChange={e => setBootstrapToken(e.target.value)}
              className="w-full border rounded px-3 py-2" />
            <input type="password" placeholder="Choose a password" value={password}
              onChange={e => setPassword(e.target.value)}
              className="w-full border rounded px-3 py-2" />
            {error && <p className="text-red-600 text-sm">{error}</p>}
            <button type="submit" className="w-full bg-blue-600 text-white rounded py-2">Continue</button>
          </form>
        )}

        {step === 'totp' && (
          <form onSubmit={submitTotp} className="space-y-4">
            <p className="text-sm text-gray-600">Scan with your authenticator app, then enter the code.</p>
            <div className="flex justify-center"><QRCodeSVG value={uri} size={180} /></div>
            <input type="text" inputMode="numeric" placeholder="6-digit code" value={code}
              onChange={e => setCode(e.target.value)}
              className="w-full border rounded px-3 py-2" />
            {error && <p className="text-red-600 text-sm">{error}</p>}
            <button type="submit" className="w-full bg-blue-600 text-white rounded py-2">Verify</button>
          </form>
        )}

        {step === 'recovery' && (
          <div className="space-y-4">
            <p className="text-sm text-gray-600">
              Save these recovery codes somewhere safe. Each can be used once in place of your authenticator.
            </p>
            <ul className="grid grid-cols-2 gap-1 font-mono text-sm bg-gray-50 p-3 rounded">
              {recovery.map(c => <li key={c}>{c}</li>)}
            </ul>
            <button onClick={onComplete} className="w-full bg-blue-600 text-white rounded py-2">
              I've saved them — continue
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
