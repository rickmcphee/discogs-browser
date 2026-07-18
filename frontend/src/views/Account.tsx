import { useRef, useState, memo } from 'react'
import Avatar from '../components/Avatar'
import { changePassword, deleteAvatar, logout, uploadAvatar } from '../api/client'

interface Props {
  avatarVersion: number
  onAvatarChange: (version: number) => void
}

function Account({ avatarVersion, onAvatarChange }: Props) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [avatarError, setAvatarError] = useState('')
  const [avatarBusy, setAvatarBusy] = useState(false)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [authCode, setAuthCode] = useState('')
  const [passwordMessage, setPasswordMessage] = useState('')

  async function handleFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setAvatarError('')
    setAvatarBusy(true)
    try {
      await uploadAvatar(file)
      onAvatarChange(Date.now())
    } catch (err: any) {
      setAvatarError(err.message || 'Upload failed')
    } finally {
      setAvatarBusy(false)
    }
  }

  async function handleRemovePhoto() {
    setAvatarError('')
    setAvatarBusy(true)
    try {
      await deleteAvatar()
      onAvatarChange(0)
    } catch (err: any) {
      setAvatarError(err.message || 'Remove failed')
    } finally {
      setAvatarBusy(false)
    }
  }

  async function submitPasswordChange() {
    try {
      await changePassword(currentPassword, newPassword, authCode)
      setPasswordMessage('Password changed.')
      setCurrentPassword('')
      setNewPassword('')
      setAuthCode('')
    } catch {
      setPasswordMessage('Failed — check current password and code.')
    }
  }

  return (
    <div className="max-w-3xl mx-auto p-6 space-y-10">
      <h1 className="text-lg font-semibold text-white text-left">Account</h1>

      {/* Avatar */}
      <section>
        <div className="flex items-center gap-4">
          <button onClick={() => fileInputRef.current?.click()} disabled={avatarBusy} aria-label="Change photo" className="group relative rounded-full">
            <Avatar version={avatarVersion} size="lg" />
            <span className="absolute inset-0 rounded-full flex items-center justify-center bg-black/0 group-hover:bg-black/40 transition-colors">
              <svg viewBox="0 0 24 24" fill="none" className="w-6 h-6 text-white opacity-0 group-hover:opacity-100">
                <path d="M4 8a2 2 0 0 1 2-2h1.5l1-1.5h7l1 1.5H18a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
                <circle cx="12" cy="13" r="3.25" stroke="currentColor" strokeWidth="1.5" />
              </svg>
            </span>
          </button>
          <div>
            <input
              ref={fileInputRef}
              data-testid="avatar-file-input"
              type="file"
              accept="image/*"
              className="hidden"
              onChange={handleFileSelected}
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={avatarBusy}
              className="text-sm text-indigo-400 hover:text-indigo-300 transition-colors"
            >
              Change photo
            </button>
            {avatarVersion !== 0 && (
              <button
                onClick={handleRemovePhoto}
                disabled={avatarBusy}
                className="block text-sm text-gray-500 hover:text-red-400 transition-colors mt-1"
              >
                Remove photo
              </button>
            )}
            {avatarError && <p className="text-xs text-red-400 mt-1">{avatarError}</p>}
          </div>
        </div>
      </section>

      {/* Account & Security */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-1 text-left">Account & Security</h2>
        <p className="text-sm text-gray-500 mb-4 text-left">
          Change your password or log out of this session.
        </p>
        <table className="w-full text-sm border-collapse">
          <tbody>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap w-40">
                <label htmlFor="account-current-password">Current password</label>
              </td>
              <td className="py-3 pr-4 text-left align-top w-64">
                <input
                  id="account-current-password"
                  type="password"
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                />
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed"></td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap">
                <label htmlFor="account-new-password">New password</label>
              </td>
              <td className="py-3 pr-4 text-left align-top">
                <input
                  id="account-new-password"
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                />
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed"></td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap">
                <label htmlFor="account-auth-code">Authenticator code</label>
              </td>
              <td className="py-3 pr-4 text-left align-top">
                <input
                  id="account-auth-code"
                  type="text"
                  inputMode="numeric"
                  value={authCode}
                  onChange={(e) => setAuthCode(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                />
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Current code from your authenticator app.
              </td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left align-top whitespace-nowrap w-40"></td>
              <td className="py-3 pr-4 text-left align-top">
                <div className="flex items-center gap-3">
                  <button
                    onClick={submitPasswordChange}
                    className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 rounded text-xs font-medium transition-colors"
                  >
                    Change password
                  </button>
                  <button
                    onClick={() => logout().then(() => window.location.reload())}
                    className="px-3 py-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded text-xs font-medium transition-colors"
                  >
                    Log out
                  </button>
                </div>
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                {passwordMessage}
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  )
}

export default memo(Account)
