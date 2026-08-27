import { useEffect, useRef, useState, memo } from 'react'
import Avatar from '../components/Avatar'
import { createInvite, deleteAvatar, getUserSettings, listInvites, logout, postPlexMatchStart, saveUserSettings, uploadAvatar } from '../api/client'
import type { Invite } from '../api/types'
import { secondaryButtonClass } from '../styles/buttons'
import { textInputClass } from '../styles/inputs'
import { stackedTableClass, stackedBodyClass, stackedRowClass, stackedCellClass } from '../styles/tables'

// apiFetch throws Error(await r.text()), so err.message is FastAPI's raw JSON
// body for a handled error and a plain string for anything else. Every branch
// narrows to a string before returning: `detail` is only a string for the
// app's own HTTPExceptions -- FastAPI's own 422s make it an array of objects,
// which React throws on rendering -- and a rejection carrying no message at
// all must reach the fallback rather than throwing here.
function errorMessage(err: unknown, fallback: string): string {
  const raw = typeof (err as { message?: unknown })?.message === 'string'
    ? (err as { message: string }).message
    : typeof err === 'string' ? err : ''
  try {
    const parsed = JSON.parse(raw)
    if (typeof parsed?.detail === 'string' && parsed.detail) return parsed.detail
  } catch {
    // not JSON, use raw message
  }
  return raw || fallback
}

// Postgres TIMESTAMP (not TIMESTAMPTZ) columns serialize as offsetless ISO
// strings -- `new Date()` on those parses as browser-local time, not UTC.
function formatServerTimestamp(iso: string): string {
  const hasOffset = /[zZ]|[+-]\d\d:\d\d$/.test(iso)
  return new Date(hasOffset ? iso : `${iso}Z`).toLocaleString()
}

interface Props {
  avatarVersion: number
  onAvatarChange: (version: number) => void
  isAdmin?: boolean
  viewingAsUser?: boolean
  onToggleViewAsUser?: () => void
  onRefreshRecommendations?: () => void
  onExportRecommendations?: () => void
  onImportRecommendations?: (file: File) => void
  onClearRecommendations?: () => void
  hasJudgedItems?: boolean
}

function Account({
  avatarVersion,
  onAvatarChange,
  isAdmin = false,
  viewingAsUser = false,
  onToggleViewAsUser = () => {},
  onRefreshRecommendations = () => {},
  onExportRecommendations = () => {},
  onImportRecommendations = () => {},
  onClearRecommendations = () => {},
  hasJudgedItems = false,
}: Props) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const importInputRef = useRef<HTMLInputElement>(null)
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
  const [invites, setInvites] = useState<Invite[]>([])
  const [invitesLoading, setInvitesLoading] = useState(true)
  const latestInvitesSeq = useRef(0)
  const [invitesError, setInvitesError] = useState('')
  const [inviteNote, setInviteNote] = useState('')
  const [mintedCode, setMintedCode] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [minting, setMinting] = useState(false)

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

  useEffect(() => {
    if (!isAdmin || viewingAsUser) return
    const seq = ++latestInvitesSeq.current
    listInvites()
      .then((result) => {
        if (seq !== latestInvitesSeq.current) return
        setInvites(result)
        setInvitesLoading(false)
      })
      .catch((err) => {
        if (seq !== latestInvitesSeq.current) return
        setInvitesError(errorMessage(err, 'Could not load invites'))
        setInvitesLoading(false)
      })
  }, [isAdmin, viewingAsUser])

  async function handleGenerateInvite() {
    setInvitesError('')
    setMinting(true)
    try {
      try {
        const { code } = await createInvite(inviteNote.trim() || undefined)
        setMintedCode(code)
        setCopied(false)
        setInviteNote('')
      } catch (err) {
        setInvitesError(errorMessage(err, 'Could not generate invite'))
        return
      }
      // separate from the mint's catch: the code is already minted, so a failed
      // refetch must not be reported as a failed mint
      const seq = ++latestInvitesSeq.current
      try {
        const result = await listInvites()
        if (seq !== latestInvitesSeq.current) return
        setInvites(result)
        setInvitesLoading(false)
      } catch {
        if (seq !== latestInvitesSeq.current) return
        setInvitesError('Invite created, but the list could not be refreshed.')
      }
    } finally {
      setMinting(false)
    }
  }

  async function handleCopyInvite(code: string) {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
    } catch {
      setInvitesError('Could not copy to the clipboard. Select the code above and copy it manually.')
    }
  }

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

  const handleImportFileSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    // Reset before handing the file off, so selecting the same file twice in
    // a row still fires a change event.
    e.target.value = ''
    if (file) onImportRecommendations(file)
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
    <div className="max-w-3xl mx-auto p-4 space-y-8 md:p-6 md:space-y-10">
      {/* Avatar */}
      <section>
        <div className="flex flex-col items-start gap-4 sm:flex-row sm:items-center sm:justify-between">
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
                className="text-sm text-gray-400 hover:text-white transition-colors"
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
          <div className="flex w-full items-center gap-4 sm:w-auto">
            {isAdmin && (
              <div className="flex items-center gap-2">
                <span className="text-sm text-gray-400">{viewingAsUser ? 'User' : 'Admin'}</span>
                <button
                  role="switch"
                  aria-checked={viewingAsUser}
                  aria-label="Toggle admin/user view"
                  onClick={onToggleViewAsUser}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                    viewingAsUser ? 'bg-gray-600' : 'bg-gray-800'
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
            <button
              onClick={() => {
                logout().then(() => {
                  localStorage.removeItem('discogs-browser.viewAsUser')
                  window.location.reload()
                }).catch(() => {})
              }}
              className={`ml-auto px-3 py-1 text-xs sm:ml-0 ${secondaryButtonClass()}`}
            >
              Log out
            </button>
          </div>
        </div>
      </section>

      {/* Recommendations */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-1 text-left">Recommendations</h2>
        <p className="text-sm text-gray-500 mb-4 text-left">
          Used to judge Store items against your collection for the Recommended filter.
        </p>
        <table className={stackedTableClass}>
          <tbody className={stackedBodyClass}>
            <tr className={`border-b border-gray-800/50 ${stackedRowClass}`}>
              <td className={`pt-3 pb-1 text-left text-gray-300 font-medium align-top whitespace-nowrap md:py-3 md:pr-4 md:w-40 ${stackedCellClass}`}>
                Anthropic API key
              </td>
              <td className={`pb-2 text-left align-top md:py-3 md:pr-4 md:w-64 ${stackedCellClass}`}>
                <input
                  type="password"
                  aria-label="Anthropic API key"
                  value={anthropicApiKey}
                  placeholder="sk-ant-..."
                  onChange={(e) => setAnthropicApiKey(e.target.value)}
                  className={`w-full px-3 py-1 ${textInputClass()}`}
                />
              </td>
              <td className={`pb-3 text-left text-gray-500 text-xs align-top leading-relaxed md:py-3 ${stackedCellClass}`}>
                Get one at platform.claude.com.
              </td>
            </tr>
            <tr className={`border-b border-gray-800/50 ${stackedRowClass}`}>
              <td className={`pt-3 pb-1 text-left text-gray-300 font-medium align-top whitespace-nowrap md:py-3 md:pr-4 ${stackedCellClass}`}>
                Recommendation item limit
              </td>
              <td className={`pb-2 text-left align-top md:py-3 md:pr-4 ${stackedCellClass}`}>
                <input
                  type="number"
                  min={0}
                  aria-label="Recommendation item limit"
                  value={recommendationItemLimit}
                  onChange={(e) => setRecommendationItemLimit(Math.max(0, parseInt(e.target.value) || 0))}
                  className={`w-24 px-3 py-1 ${textInputClass()}`}
                />
              </td>
              <td className={`pb-3 text-left text-gray-500 text-xs align-top leading-relaxed md:py-3 ${stackedCellClass}`}>
                Maximum number of unprocessed Store items evaluated by Claude for recommendation each time. Extra items are evaluated on a later run. 0 = no limit.
              </td>
            </tr>
            <tr className={`border-b border-gray-800/50 ${stackedRowClass}`}>
              <td className="hidden md:table-cell md:py-3 md:pr-4 md:align-top md:w-40"></td>
              <td className={`pb-2 text-left align-top md:py-3 md:pr-4 ${stackedCellClass}`}>
                <button
                  onClick={onRefreshRecommendations}
                  className={`w-20 text-center px-3 py-1 text-xs disabled:opacity-50 ${secondaryButtonClass()}`}
                >
                  Refresh
                </button>
              </td>
              <td className={`pb-3 text-left text-gray-500 text-xs align-top leading-relaxed md:py-3 ${stackedCellClass}`}>
                Evaluate unprocessed Store items for recommendation, without a full catalog re-crawl.
              </td>
            </tr>
            <tr className={`border-b border-gray-800/50 ${stackedRowClass}`}>
              <td className="hidden md:table-cell md:py-3 md:pr-4 md:align-top md:w-40"></td>
              <td className={`pb-2 text-left align-top md:py-3 md:pr-4 ${stackedCellClass}`}>
                <button
                  onClick={onExportRecommendations}
                  disabled={!hasJudgedItems}
                  className={`w-20 text-center px-3 py-1 text-xs disabled:opacity-50 ${secondaryButtonClass()}`}
                >
                  Export
                </button>
              </td>
              <td className={`pb-3 text-left text-gray-500 text-xs align-top leading-relaxed md:py-3 ${stackedCellClass}`}>
                Download every judgment — recommended and not — as CSV (artist, title, format,
                price, source, link, reason, item_key, recommended, judged_at). Keep it as a
                backup: it can be imported here or into another instance without paying to
                re-evaluate.
              </td>
            </tr>
            <tr className={`border-b border-gray-800/50 ${stackedRowClass}`}>
              <td className="hidden md:table-cell md:py-3 md:pr-4 md:align-top md:w-40"></td>
              <td className={`pb-2 text-left align-top md:py-3 md:pr-4 ${stackedCellClass}`}>
                <input
                  ref={importInputRef}
                  data-testid="recommendations-import-input"
                  type="file"
                  accept=".csv,text/csv"
                  className="hidden"
                  onChange={handleImportFileSelected}
                />
                <button
                  onClick={() => importInputRef.current?.click()}
                  className={`w-20 text-center px-3 py-1 text-xs ${secondaryButtonClass()}`}
                >
                  Import
                </button>
              </td>
              <td className={`pb-3 text-left text-gray-500 text-xs align-top leading-relaxed md:py-3 ${stackedCellClass}`}>
                Load a recommendations CSV exported from this or another instance, so judgments
                you already paid for aren't re-evaluated. Each item keeps whichever verdict was
                judged most recently. Imported items that aren't in stock right now take effect
                the next time a Store sync sees them. Judgments reflect the taste of the
                collection they were made against, so a file from someone else carries their
                preferences, not yours.
              </td>
            </tr>
            <tr className={stackedRowClass}>
              <td className="hidden md:table-cell md:py-3 md:pr-4 md:align-top md:w-40"></td>
              <td className={`pb-2 text-left align-top md:py-3 md:pr-4 ${stackedCellClass}`}>
                <button
                  onClick={onClearRecommendations}
                  disabled={!hasJudgedItems}
                  className={`w-20 text-center px-3 py-1 text-xs disabled:opacity-50 ${secondaryButtonClass()}`}
                >
                  Clear
                </button>
              </td>
              <td className={`pb-3 text-left text-gray-500 text-xs align-top leading-relaxed md:py-3 ${stackedCellClass}`}>
                Remove all recommendation judgments, recommended and not-recommended, so every Store item is re-evaluated from scratch on the next run.
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
        <table className={stackedTableClass}>
          <tbody className={stackedBodyClass}>
            <tr className={`border-b border-gray-800/50 ${stackedRowClass}`}>
              <td className={`pt-3 pb-1 text-left text-gray-300 font-medium align-top whitespace-nowrap md:py-3 md:pr-4 md:w-40 ${stackedCellClass}`}>
                Plex server address
              </td>
              <td className={`pb-2 text-left align-top md:py-3 md:pr-4 md:w-64 ${stackedCellClass}`}>
                <input
                  type="text"
                  aria-label="Plex server address"
                  value={plexBaseUrl}
                  placeholder="https://1-2-3-4.abcd1234.plex.direct:32400"
                  onChange={(e) => setPlexBaseUrl(e.target.value)}
                  className={`w-full px-3 py-1 ${textInputClass()}`}
                />
              </td>
              <td className={`pb-3 text-left text-gray-500 text-xs align-top leading-relaxed md:py-3 ${stackedCellClass}`}>
                Must be reachable from this server over the public internet —
                enable Plex Remote Access on your server and use the
                plex.direct address it gives you. A LAN address, or a private
                tunnel address (e.g. Tailscale), won't work. Must start with
                https:// — a plain http:// address is rejected, since your
                Plex token would otherwise cross the internet unencrypted.
              </td>
            </tr>
            <tr className={`border-b border-gray-800/50 ${stackedRowClass}`}>
              <td className={`pt-3 pb-1 text-left text-gray-300 font-medium align-top whitespace-nowrap md:py-3 md:pr-4 ${stackedCellClass}`}>
                Plex token
              </td>
              <td className={`pb-2 text-left align-top md:py-3 md:pr-4 ${stackedCellClass}`}>
                <input
                  type="password"
                  aria-label="Plex token"
                  value={plexToken}
                  placeholder="your Plex token"
                  onChange={(e) => setPlexToken(e.target.value)}
                  className={`w-full px-3 py-1 ${textInputClass()}`}
                />
              </td>
              <td className={`pb-3 text-left text-gray-500 text-xs align-top leading-relaxed md:py-3 ${stackedCellClass}`}>
                Find it via a browser request while logged into Plex Web (see Plex support docs).
              </td>
            </tr>
            <tr className={`border-b border-gray-800/50 ${stackedRowClass}`}>
              <td className={`pt-3 pb-1 text-left text-gray-300 font-medium align-top whitespace-nowrap md:py-3 md:pr-4 ${stackedCellClass}`}>
                Match threshold
              </td>
              <td className={`pb-2 text-left align-top md:py-3 md:pr-4 ${stackedCellClass}`}>
                <input
                  type="number"
                  min={0}
                  max={100}
                  aria-label="Match threshold"
                  value={plexMatchThreshold}
                  onChange={(e) => setPlexMatchThreshold(Math.max(0, Math.min(100, parseInt(e.target.value) || 0)))}
                  className={`w-24 px-3 py-1 ${textInputClass()}`}
                />
              </td>
              <td className={`pb-3 text-left text-gray-500 text-xs align-top leading-relaxed md:py-3 ${stackedCellClass}`}>
                Minimum fuzzy-match score (0–100) for a release to be linked to a Plex album. Default 90.
              </td>
            </tr>
            <tr className={stackedRowClass}>
              <td className="hidden md:table-cell md:py-3 md:pr-4 md:align-top"></td>
              <td className={`pb-2 text-left align-top md:py-3 md:pr-4 ${stackedCellClass}`}>
                <button
                  onClick={handleLinkPlexNow}
                  disabled={!plexBaseUrl || !plexToken || plexMatchStarting}
                  className={`px-3 py-1 text-xs disabled:opacity-50 ${secondaryButtonClass()}`}
                >
                  {plexMatchStarting ? 'Starting…' : 'Link Now'}
                </button>
              </td>
              <td className={`pb-3 text-left text-gray-500 text-xs align-top leading-relaxed md:py-3 ${stackedCellClass}`}>
                Re-run Plex matching against your current collection now, without waiting for the next sync.
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      {isAdmin && !viewingAsUser && (
        <section>
          <h2 className="text-lg font-semibold text-white mb-1 text-left">Invites</h2>
          <p className="text-sm text-gray-500 mb-4 text-left">
            Mint a code for someone to sign up with. Anyone holding the code can redeem it once.
          </p>
          {invitesError && <p className="text-xs text-red-400 mb-3 text-left">{invitesError}</p>}
          <div className="flex items-center gap-2 mb-4">
            <input
              type="text"
              aria-label="Invite note"
              value={inviteNote}
              placeholder="Optional note (e.g. who this is for)"
              onChange={(e) => setInviteNote(e.target.value)}
              className={`flex-1 px-3 py-1 ${textInputClass()}`}
            />
            <button onClick={handleGenerateInvite} disabled={minting} className={`px-3 py-1 text-xs disabled:opacity-50 ${secondaryButtonClass()}`}>
              {minting ? 'Generating…' : 'Generate'}
            </button>
          </div>
          {mintedCode && (
            <p className="text-sm text-gray-300 mb-4 text-left">
              <span className="font-mono">{mintedCode}</span>
              <button
                onClick={() => handleCopyInvite(mintedCode)}
                className={`ml-2 px-2 py-0.5 text-xs ${secondaryButtonClass()}`}
              >
                {copied ? 'Copied' : 'Copy'}
              </button>
            </p>
          )}
          {invites.length === 0 ? (
            !invitesLoading && !invitesError && (
              <p className="text-gray-500 text-sm text-left">No invites minted yet.</p>
            )
          ) : (
            <div className="-mx-4 overflow-x-auto px-4 md:mx-0 md:px-0">
            <table className="w-full min-w-[36rem] text-sm border-collapse">
              <thead>
                <tr className="text-xs text-gray-500 uppercase tracking-wider border-b border-gray-800">
                  <th className="text-left py-2 pr-4">Code</th>
                  <th className="text-left py-2 pr-4">Note</th>
                  <th className="text-left py-2 pr-4">Created by</th>
                  <th className="text-left py-2 pr-4">Created at</th>
                  <th className="text-left py-2 pr-4">Redeemed by</th>
                  <th className="text-left py-2">Redeemed at</th>
                </tr>
              </thead>
              <tbody>
                {invites.map((invite) => (
                  <tr key={invite.code} className="border-b border-gray-800/50">
                    <td className="py-2 pr-4 text-left font-mono text-xs text-gray-300">{invite.code}</td>
                    <td className="py-2 pr-4 text-left text-gray-400">{invite.note || '—'}</td>
                    <td className="py-2 pr-4 text-left text-gray-400">{invite.created_by_username || '—'}</td>
                    <td className="py-2 pr-4 text-left text-gray-500 text-xs">{formatServerTimestamp(invite.created_at)}</td>
                    <td className="py-2 pr-4 text-left text-gray-400">{invite.redeemed_by_username || '—'}</td>
                    <td className="py-2 text-left text-gray-500 text-xs">
                      {invite.redeemed_at ? formatServerTimestamp(invite.redeemed_at) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
        </section>
      )}
    </div>
  )
}

export default memo(Account)
