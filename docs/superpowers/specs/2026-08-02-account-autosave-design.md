# Account Auto-Save — Design Spec

_2026-08-02_

## Overview

`Account.tsx`'s Recommendations and Plex sections each have a "Save" button, but both buttons call the exact same handler (`handleSaveUserSettings`), which sends all 5 fields — `anthropic_api_key`, `recommendation_item_limit`, `plex_base_url`, `plex_token`, `plex_match_threshold` — in one combined `POST /api/user-settings` call (`backend/routers/settings.py:92-107` requires and overwrites all 5 fields together; there's no partial-update support). The two buttons are UI redundancy for the same one action, not two independent saves.

This spec replaces both buttons with a single debounced auto-save: any edit to any of the 5 fields schedules a save 800ms after the user stops typing, using the exact same combined save call that exists today. No backend changes.

## Goals / non-goals

**Goals**
- Remove both "Save" buttons ("Save Recommendations settings", "Save Plex settings").
- Auto-save all 5 fields together, 800ms after the last edit to any of them, matching the backend's existing full-object-overwrite contract.
- No success indicator ("Saving…"/"✓ Saved") — saves happen silently.
- Save failures (currently only the Plex address reachability check can fail server-side) still surface inline as red text, same treatment as today. The error clears as soon as the user edits any field again, and reappears only if the next save attempt also fails.
- Remove the now-dead `userSettingsSaving`/`userSettingsSaved` state entirely.
- Simplify each section header back to a plain `<h2>` (the `flex items-center justify-between` wrapper existed only to hold the button next to the heading).

**Non-goals**
- No backend changes — the combined save call and its validation are unchanged.
- No per-field or per-section save granularity. The backend takes all 5 fields in one request; splitting that is out of scope and not requested.
- No debounce-cancel-on-unmount handling. `Account` is never actually unmounted while the app is running — `App.tsx` keeps every view mounted and only toggles a `hidden` class — so a pending debounce timer keeps running (and will still fire and save) even while the user is on a different tab. The only edit that can be lost is one made in the last 800ms before a full page reload (e.g. the "Log out" button's `window.location.reload()`), which is an accepted, standard limitation of debounced auto-save, not a regression — today, forgetting to click Save loses the edit unconditionally.

## Frontend changes

**`frontend/src/views/Account.tsx`**

- Delete `userSettingsSaving`/`userSettingsSaved` state and every reference to them (the `setTimeout(() => setUserSettingsSaved(false), 2000)` line, the button `disabled={userSettingsSaving}` props, and the `{userSettingsSaved ? '✓ Saved' : userSettingsSaving ? 'Saving…' : 'Save'}` button label — the buttons themselves are deleted, see below).
- Add a `skipNextAutoSave` ref (`useRef(true)`, starting `true`). The debounce effect (below) runs once on mount with the fields' default blank/placeholder values, and once again when `getUserSettings()` resolves and populates them — neither of those two runs is a real edit, so both must be skipped. The ref starts `true` to skip the mount-time run, and is set back to `true` a second time inside the existing `getUserSettings().then(...)` callback (line 24-30, after the 5 `set*` calls) to also skip the fetch-triggered run. (A ref that's only set `true` once, after the fetch resolves, doesn't work: by the time the fetch-triggered render's effects run, the ref would already read `true` from that single assignment, which reads as "loaded" rather than "skip this one run" — it wouldn't distinguish the fetch-triggered run from any later real edit. Re-arming the flag specifically at the moment the fetch resolves is what makes the very next debounce-effect run — and only that one — a no-op.)
- Add a `settingsLoaded` boolean state (`useState(false)`), set `true` in the same `getUserSettings().then(...)` callback, alongside the `skipNextAutoSave.current = true` re-arm, and added to the debounce effect's dependency array (below). This closes a gap the re-arm alone doesn't cover: if the fetched values happen to equal the fields' current `useState` defaults (e.g. an account with no settings saved yet), React bails out of re-rendering on those `setState` calls (`Object.is` sees no change), so the debounce effect's fetch-triggered run — the one meant to consume the re-armed flag — never actually happens. The re-armed flag would then sit unconsumed until the user's first real edit, which gets mistaken for the skip-run and silently drops that edit's save. `settingsLoaded` guarantees a state transition (and therefore a render/effect run) happens on load regardless of whether the other 5 values changed from their defaults, so the flag is reliably consumed before any real edit can occur.
- Replace `handleSaveUserSettings` (lines 33-58) with:
  ```tsx
  async function saveUserSettingsNow() {
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
      let message = err.message || 'Save failed'
      try {
        const parsed = JSON.parse(err.message)
        if (parsed.detail) message = parsed.detail
      } catch {
        // not JSON, use raw message
      }
      setPlexSaveError(message)
    }
  }
  ```
  (Identical body to today's `handleSaveUserSettings`, minus the `userSettingsSaving`/`userSettingsSaved` bookkeeping.)
- Add a new effect, placed after the existing load effect:
  ```tsx
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
  ```
  This is the entire auto-save mechanism: one shared debounce across all 5 fields (plus `settingsLoaded`, present purely to guarantee the fetch-triggered run above actually happens — its value is never read here). The first two times this effect runs (mount, then the fetch populating real values) it just consumes the `skipNextAutoSave` flag and returns; every run after that is a real edit, so it schedules a save. React runs this effect's cleanup (`clearTimeout(timer)`) before every re-run triggered by a dependency change, which is what gives the debounce its "reset on every keystroke" behavior. Clearing `plexSaveError` in the same effect body means any edit clears a stale error instantly, before the new timer even starts.
- Recommendations section header (lines 137-146): replace the `flex items-center justify-between mb-1` wrapper + `<h2>` + Save button with a plain heading, matching "Account & Security"'s style:
  ```tsx
  <h2 className="text-lg font-semibold text-white mb-1 text-left">Recommendations</h2>
  ```
- Plex section header (lines 195-204): same simplification:
  ```tsx
  <h2 className="text-lg font-semibold text-white mb-1 text-left">Plex</h2>
  ```
- The `plexSaveError` inline error paragraph (line 209) is unchanged in place/styling — still renders below the Plex section's description, above the table.

## Data flow / lifecycle

1. On mount, the debounce effect's first run consumes the initial `skipNextAutoSave` flag and does nothing. `getUserSettings()` then populates all 5 fields, re-arms `skipNextAutoSave`, and sets `settingsLoaded` to `true` — the last of which guarantees the effect's second run actually happens (and consumes the re-armed flag) even in the edge case where the fetched values are identical to the fields' defaults and would otherwise never trigger a re-render on their own.
2. Every run after that is a real edit: the debounce effect re-runs whenever any of the 5 values changes (any keystroke), clearing the previous pending timer and scheduling a new one 800ms out.
3. When a timer actually fires (800ms with no further edits), `saveUserSettingsNow()` sends the current snapshot of all 5 fields in one `POST /api/user-settings` call — identical payload shape to today's manual save.
4. A failed save shows `plexSaveError` inline; the next edit to any field clears it immediately, and the following debounced save attempt will show it again if it still fails.

## Testing

- `frontend/src/test/account.test.tsx`:
  - `'renders anthropic API key field and saves it'`: replace the `fireEvent.click(screen.getByRole('button', { name: 'Save Recommendations settings' }))` step with nothing — after `fireEvent.change` on the Anthropic API key input, `waitFor(() => expect(saveUserSettings).toHaveBeenCalledWith(expect.objectContaining({ anthropic_api_key: 'sk-ant-new-key' })), { timeout: 2000 })` (real timers; 2000ms comfortably clears the 800ms debounce).
  - `'shows a clean error message (not raw JSON) when saving Plex settings fails'`: replace the `fireEvent.click(screen.getByRole('button', { name: 'Save Plex settings' }))` step with a `fireEvent.change` on any Plex field (e.g. Plex server address) to trigger the debounced save, then `waitFor` the error text with the same extended timeout.
  - New case: typing in a field does not call `saveUserSettings` immediately (assert it hasn't been called right after `fireEvent.change`, before the debounce elapses).
  - New case: the initial `getUserSettings()` load does not itself trigger a save (assert `saveUserSettings` is never called if no field is touched after mount, waiting past the 800ms window).
  - New case: editing a field after a failed save clears the inline error right away, before the next save attempt fires.
  - New case: an explicit regression test for the `settingsLoaded` fix — mocks `getUserSettings()` to resolve with values equal to the fields' defaults (independent of whatever the file's shared top-of-file mock happens to return, so a future change to that shared mock can't silently drop this coverage), then confirms the first real edit after load still triggers a save.
  - Existing avatar/logout tests are unaffected — different section, no shared state.
- Manual verification: open Account, edit the Anthropic API key, wait under a second, confirm (via network tab) a save fires with no visible UI change; edit the Plex address to an unreachable value, wait, confirm the inline error appears; edit it again to a valid value, confirm the error clears immediately, and confirm no error reappears after the next successful save.
