import { discogsLoginUrl } from '../api/client'

export default function LoginScreen() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-100">
      <div className="bg-white p-8 rounded shadow w-80 space-y-4 text-center">
        <h1 className="text-xl font-semibold">Sign In</h1>
        <a
          href={discogsLoginUrl()}
          className="block w-full bg-blue-600 text-white rounded py-2 hover:bg-blue-700"
        >
          Continue with Discogs
        </a>
      </div>
    </div>
  )
}
