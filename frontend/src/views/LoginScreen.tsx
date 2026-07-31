import { discogsLoginUrl } from '../api/client'

export default function LoginScreen() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-950">
      <div className="bg-gray-900 border border-gray-700 rounded-lg shadow-xl p-8 w-80 space-y-4 text-center">
        <h1 className="text-lg font-semibold text-white">Sign In</h1>
        <a
          href={discogsLoginUrl()}
          className="block w-full bg-indigo-600 hover:bg-indigo-500 text-white rounded py-2 text-sm font-medium transition-colors"
        >
          Continue with Discogs
        </a>
      </div>
    </div>
  )
}
