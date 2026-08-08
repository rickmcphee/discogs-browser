import { discogsLoginUrl } from '../api/client'
import { primaryButtonClass } from '../styles/buttons'
import TornadoBackground from '../components/TornadoBackground'

export default function LoginScreen() {
  return (
    <div className="relative min-h-screen overflow-hidden flex items-center justify-center bg-gray-950">
      <div className="absolute inset-0 w-full h-full text-gray-500 opacity-[0.4] pointer-events-none">
        <TornadoBackground />
      </div>
      <div className="relative z-10 bg-gray-900 border border-gray-700 rounded-xl shadow-xl p-8 w-80 space-y-4 text-center">
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
