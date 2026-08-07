import { discogsLoginUrl } from '../api/client'
import { primaryButtonClass } from '../styles/buttons'

export default function LoginScreen() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-950">
      <div className="bg-gray-900 border border-gray-700 rounded-xl shadow-xl p-8 w-80 space-y-4 text-center">
        <h1 className="text-base font-semibold text-white">Sign In</h1>
        <a
          href={discogsLoginUrl()}
          className={`block w-full py-2 text-sm ${primaryButtonClass()}`}
        >
          Continue with Discogs
        </a>
      </div>
    </div>
  )
}
