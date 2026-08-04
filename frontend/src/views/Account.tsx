import { useEffect, useRef, useState, memo } from 'react'
import Avatar from '../components/Avatar'
import { deleteAvatar, getUserSettings, logout, postPlexMatchStart, saveUserSettings, uploadAvatar } from '../api/client'

interface Props {
  avatarVersion: number
  onAvatarChange: (version: number) => void
  isAdmin?: boolean
  viewingAsUser?: boolean
  onToggleViewAsUser?: () => void
}

function Account({
  avatarVersion,
  onAvatarChange,
  isAdmin = false,
  viewingAsUser = false,
  onToggleViewAsUser = () => {},
}: Props) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const skipNextAutoSave = useRef(true)
  const saveChainRef = useRef<Promise<void>>(Promise.resolve())
  const latestSaveSeq = useRef(0)
  const [avatarError, setAvatarError] = useState('')
  const [avatarBusy, setAvatarBusy] = useState(false)
  const [anthropicApiKey, setAnthropicApiKey] = useState('')
  const [recommendationItemLimit, setRecommendationItemLimit] = useState(300)
  const [plexBaseUrl, setPlexBaseUrl] = useState('')
  const [plexToken, setPlexToken] = useState('')
  const [plexMatchThreshold, setPlexMatchThreshold] = useState(90)
  const [plexSaveError, setPlexSaveError] = useState('')
  const [plexMatchStarting, setPlexMatchStarting] = useState(false)
  // Value never read — its only job is forcing the debounce effect below to
  // re-run once settings load, even when the fetched values equal the
  // useState defaults above and React would otherwise bail out of re-rendering.
  const [settingsLoaded, setSettingsLoaded] = useState(false)

  useEffect(() => {
    getUserSettings().then((s) => {
      setAnthropicApiKey(s.anthropic_api_key)
      setRecommendationItemLimit(s.recommendation_item_limit)
      setPlexBaseUrl(s.plex_base_url)
      setPlexToken(s.plex_token)
      setPlexMatchThreshold(s.plex_match_threshold)
      skipNextAutoSave.current = true
      setSettingsLoaded(true)
    }).catch(() => {})
  }, [])

  function saveUserSettingsNow() {
    const seq = ++latestSaveSeq.current
    saveChainRef.current = saveChainRef.current.then(async () => {
      setPlexSaveError('')
      try {
        await saveUserSettings({
          anthropic_api_key: anthropicApiKey,
          recommendation_item_limit: recommendationItemLimit,
          plex_base_url: plexBaseUrl,
          plex_token: plexToken,
          plex_match_threshold: plexMatchThreshold,
        })
      } catch (err: any) {
        if (seq !== latestSaveSeq.current) return
        let message = err.message || 'Save failed'
        try {
          const parsed = JSON.parse(err.message)
          if (parsed.detail) message = parsed.detail
        } catch {
          // not JSON, use raw message
        }
        setPlexSaveError(message)
      }
    })
  }

  useEffect(() => {
    if (skipNextAutoSave.current) {
      skipNextAutoSave.current = false
      return
    }
    setPlexSaveError('')
    const timer = setTimeout(() => {
      saveUserSettingsNow()
    }, 800)
    return () => clearTimeout(timer)
  }, [anthropicApiKey, recommendationItemLimit, plexBaseUrl, plexToken, plexMatchThreshold, settingsLoaded])

  async function handleLinkPlexNow() {
    setPlexMatchStarting(true)
    setPlexSaveError('')
    try {
      await postPlexMatchStart()
    } catch (err: any) {
      let message = err.message || 'Link Now failed'
      try {
        const parsed = JSON.parse(err.message)
        if (parsed.detail) message = parsed.detail
      } catch {
        // not JSON, use raw message
      }
      setPlexSaveError(message)
    } finally {
      setPlexMatchStarting(false)
    }
  }

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

  return (
    <div className="max-w-3xl mx-auto p-6 space-y-10">
      {/* Avatar */}
      <section>
        <div className="flex items-center justify-between gap-4">
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
          {isAdmin && (
            <div className="flex items-center gap-2">
              <span className="text-sm text-gray-400">{viewingAsUser ? 'User' : 'Admin'}</span>
              <button
                role="switch"
                aria-checked={viewingAsUser}
                aria-label="Toggle admin/user view"
                onClick={onToggleViewAsUser}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                  viewingAsUser ? 'bg-gray-600' : 'bg-indigo-600'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    viewingAsUser ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>
          )}
        </div>
      </section>

      {/* Recommendations */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-1 text-left">Recommendations</h2>
        <p className="text-sm text-gray-500 mb-4 text-left">
          Used to judge Store items against your collection for the Recommended filter.
        </p>
        <table className="w-full text-sm border-collapse">
          <tbody>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap w-40">
                Anthropic API key
              </td>
              <td className="py-3 pr-4 text-left align-top w-64">
                <input
                  type="password"
                  aria-label="Anthropic API key"
                  value={anthropicApiKey}
                  placeholder="sk-ant-..."
                  onChange={(e) => setAnthropicApiKey(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                />
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Get one at platform.claude.com.
              </td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap">
                Recommendation item limit
              </td>
              <td className="py-3 pr-4 text-left align-top">
                <input
                  type="number"
                  min={0}
                  aria-label="Recommendation item limit"
                  value={recommendationItemLimit}
                  onChange={(e) => setRecommendationItemLimit(Math.max(0, parseInt(e.target.value) || 0))}
                  className="w-24 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white focus:outline-none focus:border-indigo-500"
                />
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Maximum number of unprocessed Store items evaluated by Claude for recommendation each time. Extra items are evaluated on a later run. 0 = no limit.
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      {/* Plex */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-1 text-left">Plex</h2>
        <p className="text-sm text-gray-500 mb-4 text-left">
          Link collection releases to matching albums in your Plex music library.
        </p>
        {plexSaveError && <p className="text-xs text-red-400 mb-3 text-left">{plexSaveError}</p>}
        <table className="w-full text-sm border-collapse">
          <tbody>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap w-40">
                Plex server address
              </td>
              <td className="py-3 pr-4 text-left align-top w-64">
                <input
                  type="text"
                  aria-label="Plex server address"
                  value={plexBaseUrl}
                  placeholder="https://1-2-3-4.abcd1234.plex.direct:32400"
                  onChange={(e) => setPlexBaseUrl(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                />
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Must be reachable from this server over the public internet —
                enable Plex Remote Access on your server and use the
                plex.direct address it gives you. A LAN address, or a private
                tunnel address (e.g. Tailscale), won't work. Must start with
                https:// — a plain http:// address is rejected, since your
                Plex token would otherwise cross the internet unencrypted.
              </td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap">
                Plex token
              </td>
              <td className="py-3 pr-4 text-left align-top">
                <input
                  type="password"
                  aria-label="Plex token"
                  value={plexToken}
                  placeholder="your Plex token"
                  onChange={(e) => setPlexToken(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                />
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Find it via a browser request while logged into Plex Web (see Plex support docs).
              </td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap">
                Match threshold
              </td>
              <td className="py-3 pr-4 text-left align-top">
                <input
                  type="number"
                  min={0}
                  max={100}
                  aria-label="Match threshold"
                  value={plexMatchThreshold}
                  onChange={(e) => setPlexMatchThreshold(Math.max(0, Math.min(100, parseInt(e.target.value) || 0)))}
                  className="w-24 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white focus:outline-none focus:border-indigo-500"
                />
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Minimum fuzzy-match score (0–100) for a release to be linked to a Plex album. Default 90.
              </td>
            </tr>
            <tr>
              <td className="py-3 pr-4 text-left align-top whitespace-nowrap"></td>
              <td className="py-3 pr-4 text-left align-top">
                <button
                  onClick={handleLinkPlexNow}
                  disabled={!plexBaseUrl || !plexToken || plexMatchStarting}
                  className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 active:bg-indigo-800 disabled:opacity-50 rounded text-xs font-medium transition-colors"
                >
                  {plexMatchStarting ? 'Starting…' : 'Link Now'}
                </button>
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Re-run Plex matching against your current collection now, without waiting for the next sync.
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      {/* Account & Security */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-1 text-left">Account & Security</h2>
        <p className="text-sm text-gray-500 mb-4 text-left">
          Log out of this session.
        </p>
        <button
          onClick={() => {
            logout().then(() => {
              localStorage.removeItem('discogs-browser.viewAsUser')
              window.location.reload()
            }).catch(() => {})
          }}
          className="px-3 py-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded text-xs font-medium transition-colors"
        >
          Log out
        </button>
      </section>
    </div>
  )
}

export default memo(Account)
