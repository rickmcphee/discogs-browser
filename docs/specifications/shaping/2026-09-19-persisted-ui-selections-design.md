# Persisted UI selections design

Date: 2026-09-19
Branch: `claude/hopeful-ritchie-96zpzc`

## Problem

Reopening the app throws away most of what the user set up in it. Some
selections do survive — list vs tiles per tab, the Store filter, Cheapest —
because each grew its own `useState(() => localStorage.getItem(...))`
initialiser and a matching write effect when it was added. Everything else
resets on every visit: the tab you were on, the artist the sidebar was
narrowed to, the column you sorted by and in which direction, and the
Collection tab's Unmatched filter.

That split is arbitrary rather than considered. The list/tile toggle persists
and the sort beside it does not, so a user who reads their wantlist
newest-first re-sorts it every time, while the view mode they set once months
ago is still there. Nothing about a sort makes it less of a selection than a
view mode; it just never got its own pair of statements.

And the pair is the other half of the problem. Read and write are separate
statements, declared a hundred lines apart, with the validation — if any —
inlined at the read. The one key that validates (`stockFilter_store` against
`STORE_FILTERS`) does so because it had to learn: a rename left `'overlapping'`
in browsers that had selected it, and it came back as a filter the app no
longer had. A stored value is *input*, written by whatever build of the app the
user last visited, and every value added under the existing idiom is another
chance to forget that.

## Scope

Touches:

- `frontend/src/hooks/usePersistentState.ts` — new. `usePersistentState`, which
  reads a validated string from `localStorage` on mount and writes every later
  change back, `usePersistentFlag` for the boolean-valued ones, and the
  guarded `readStored`/`writeStored`/`removeStored` behind both, exported for
  the storage App and Account touch outside a hook.
- `frontend/src/App.tsx` — the active library tab, under
  `discogs-browser.view`. `hasPriceData` starts as `null` (unknown) rather
  than `false`; see "Decisions". The dismissed-banner ids and the view-as-user
  toggle move onto the guarded helpers, and the ids gain a finite-number
  check.
- `frontend/src/views/Account.tsx` — the log-out path's `removeItem` moves
  onto `removeStored`.
- `frontend/src/views/RecordBrowser.tsx` — artist filter, sort field, sort
  order and the Unmatched filter persist; the existing view-mode pair moves
  onto the hook.
- `frontend/src/views/StockBrowser.tsx` — artist filter, sort field and sort
  order persist; the existing view-mode, Store-filter and Cheapest pairs move
  onto the hook.
- Tests: `frontend/src/test/usePersistentState.test.tsx` (new),
  `frontend/src/test/viewPersistence.test.tsx` (new),
  `frontend/src/test/recordBrowser.test.tsx`,
  `frontend/src/test/stockBrowser.test.tsx`,
  `frontend/src/test/crawlStatusBar.test.tsx`.

What is stored, and under which key:

| Selection | Key | Values |
| --- | --- | --- |
| Active library tab | `discogs-browser.view` | `collection`, `wantlist`, `store` |
| List vs tiles | `collectionViewMode_{collection,wantlist,store}` | `list`, `tiles` |
| Artist filter | `artistFilter_{collection,wantlist,store}` | any artist label, `''` for All |
| Sort field | `sortField_{collection,wantlist,store}` | the fields that tab's headers offer |
| Sort order | `sortOrder_{collection,wantlist,store}` | `asc`, `desc` |
| Unmatched filter | `unmatchedOnly_collection` | `true`, `false` |
| Store filter | `stockFilter_store` | the `STORE_FILTERS` members |
| Cheapest only | `stockCheapest` | `true`, `false` |

Out of scope:

- **Search text and page number.** See "Decisions".
- **Following the user across devices.** These are browser-local, like every
  UI preference the app already stores. The server-side path exists — the
  source filter is a `user_hidden_crawlers` row per user — and this is
  deliberately not that; see "Decisions".
- **Namespacing keys per account.** Two accounts sharing one browser share
  these selections, which is what every existing key here already does. A
  per-user prefix would be a strict improvement and is a separate change: it
  needs the signed-in user's id at `useState`-initialiser time, which App
  learns from `getAuthStatus` a round trip after the browsers have mounted.
- **The Logs tab's level filter.** It is a live-tailing admin console, and
  defaulting to "show everything" is the right answer there — an ERROR-only
  filter restored from an investigation last week silently hides the lines an
  admin opened the tab to watch. `paused` and the message regex beside it are
  transient for the same reason.
- **The Queue tab's drill-down.** A selected state bucket and a selected row
  are a position within data that has since been drained and refilled, not a
  selection.

## Decisions

- **One hook, not another initialiser/effect pair per value.** The existing
  idiom costs two statements far apart in the file, and the read is where
  validation has to live, so the natural way to write it is the way that
  forgets. `usePersistentState(key, fallback, parse)` makes the validation an
  argument that cannot be omitted: there is no overload that trusts the stored
  string. The `parse` returns `null` for "don't recognise this", and the hook
  falls back — with `??` rather than `||`, because `''` is the artist filter's
  "All" and a legitimate stored value.

- **The write is gated on the same `parse` as the read.** A value the hook
  would refuse to restore is not stored, so it cannot displace the value that
  would be. This is what makes the tab restore read correctly: `view` also
  holds Account and Notifications, which are errands rather than places, and
  while one of those is open the stored tab stays the library tab the user was
  last on. For every other caller the gate is a no-op — their state cannot
  hold a value their own `parse` rejects.

- **Only the tabs every user has.** `logs` and `queue` are mounted only for
  an admin (and an admin viewing as a user is not one), so a restored `logs`
  would open the app on a blank screen with no tab lit — the failure is
  invisible rather than merely wrong. `settings` renders for anyone but is a
  place you go to change something. So the parse accepts the `LIBRARY_TABS`
  views and nothing else, which also means the stored value stays valid if the
  admin tabs change.

- **What is restored is a choice, not a position.** A filter, a sort and a
  view mode are answers to "how do I want to read this", and they are all
  visible in the chrome once restored: the dropdown names the filter, the
  header carries the arrow, the sidebar highlights the artist. A page number
  is not a choice — page 3 of a catalog a sync has since reordered is a
  different set of records, and restoring it opens the app somewhere the user
  never chose to be. Search text is the interesting case: it *is* visible in
  the input, but it is typed rather than selected, and a query narrowing the
  catalog to nothing is the most common way to make the app look broken. Both
  stay per-visit.

- **The artist filter is validated by the code that already validates
  artists.** Its `parse` accepts any string, because the set of legal values
  is a list the API has not returned yet at mount. `reconcileSelectedArtist`
  already runs on every artist-list arrival to re-case a label the catalog has
  respelled or clear one that is gone (see
  [`2026-09-13-artist-punctuation-fold-design.md`](2026-09-13-artist-punctuation-fold-design.md)),
  and a restored selection is indistinguishable to it from one made before a
  sync. So a stored artist who has since left the collection clears itself on
  first load, through `selectArtist('')` — the same "back to All" transition,
  sort derivation included, that the sidebar's own All button takes.

- **A restored Price sort has to be restored with the filter it belongs to.**
  In `StockBrowser` the Price column exists only under the Collection filter —
  the discogs price is what the user paid, and only a collection row has one —
  and the backend silently degrades a `discogs_price` sort to artist order
  outside that scope. Restoring the pair independently would therefore leave
  the state claiming an order the rows are not in, with no visible control
  claiming it either, which is the invisible-filter failure `changeFilter`
  already goes out of its way to avoid. So the sort's `parse` reads the
  restored filter and refuses `discogs_price` unless it is Collection. It does
  this in the parse rather than in an effect on purpose: an effect would let
  one `load()` go out under the sort it is about to reset.

- **`hasPriceData` starts unknown, not absent.** Both browsers already reset a
  `discogs_price` sort when `hasPriceField` goes false, so that a sort cannot
  outlive the column claiming it. But App learns whether the user has any
  price data from `getPriceStatus`, a round trip after mount, and the state
  behind it started `false` — so that effect fired on every first render and
  would have reset every restored Price sort before the answer arrived. A
  Price sort that silently never persists is worse than one that does not
  persist at all, so the state starts `null` and the reset waits for a
  definite `false`. Every render gate on it is already falsy-checked, so
  "unknown" hides the column exactly as "no prices" did, and a failed
  `getPriceStatus` leaves it hidden as before.

- **Existing keys keep their names.** `collectionViewMode_store` is named for
  a component that once served a Track tab as well (see
  [`2026-09-06-track-tab-fold-design.md`](2026-09-06-track-tab-fold-design.md))
  and `stockCheapest` carries no scope at all, so a tidier scheme is easy to
  imagine. It is not worth it: renaming a key resets that selection for
  everyone who has one, which is the whole point of the feature, and the
  value is not carried forward by anything. The new keys follow the
  `name_scope` shape the newer of the existing ones use.

- **Per browser, not per user.** `localStorage` is the right store for a
  preference that describes a device: the phone wants tiles and the desktop
  wants the table, and a server-side selection would fight that on every
  switch. It is the wrong store for a preference that has to follow the user,
  which is why the source filter moved off it and into a per-user table — it
  decides which sources the *account* sees, and a user who hid the
  marketplaces on one machine has not asked to see them again on another.
  These are the former kind. The cost is that two accounts sharing a browser
  share them, which is the status quo for every key here.

- **Storage can throw, and a throw in a `useState` initialiser is a blank
  page.** Safari's private mode, a browser configured to block site data and a
  full quota all raise rather than returning `null`, and the existing
  initialisers read `localStorage` unguarded at the top of App's render. The
  guards swallow every direction: a preference that does not survive the visit
  is a far smaller failure than an app that does not start. Nothing is
  reported to the user, because there is nothing for them to do about it and
  the selection still works for as long as the tab is open.

  The guard has to cover **every** storage access in a render path, not just
  the ones behind the hook, or the property is not one the app has. So
  `readStored`/`writeStored`/`removeStored` are exported for the values App
  keeps outside it — the two dismissed-banner ids and the view-as-user toggle,
  which are not selections restored into a control — and one of those
  initialisers left unguarded aborts App's render exactly as before. The
  dismissed ids are validated on the way in for the same reason the selections
  are: `Number('banana')` is `NaN`, every `id > dismissed` comparison against
  `NaN` is false, and one corrupt value hid that banner for good with nothing
  in the UI to undo it.

  Testing this needs a note of its own, because the obvious way does not
  work: `localStorage` takes a `vi.spyOn(window.localStorage, …)` without ever
  consulting it, since a storage method lives on its object's prototype. A
  test written that way passes whether or not anything is guarded — it did
  here, on the first attempt, until a mutation check caught it. Nor can the
  spy simply name `Storage.prototype`: `setup.ts` installs a `MemoryStorage`
  fallback where jsdom leaves `localStorage` undefined, and implementing the
  `Storage` *type* does not put that class in `Storage.prototype`'s chain at
  runtime — so naming it would restore the vacuum in exactly the environment
  the fallback exists for. The spy goes on
  `Object.getPrototypeOf(window.localStorage)`, and a test of its own asserts
  the helper really does make storage throw.

- **Storage is read once, on mount; `parse` is not mount-only.** The read
  happens in the `useState` initialiser, so the key is expected to be fixed
  for the component's lifetime — which holds for every caller: the literals,
  and the `scope` prop that App pins per mounted instance (both
  `RecordBrowser`s stay mounted for the session, one per scope, hidden by CSS
  rather than unmounted). A key that changed later would write the current
  value under the new name without reading what is stored there — documented
  at the hook rather than defended against, since defending against it means
  re-reading storage mid-session and clobbering state the user is looking at.
  `parse` itself runs again on every render, to gate the write; that is
  deliberate and is what lets `StockBrowser`'s sort parse answer for the
  filter that is active now rather than the one captured at mount.

## Testing

`usePersistentState.test.tsx` covers the hook directly: an unrecognised
stored value falls back, `''` survives a round trip, a value the `parse`
rejects is not written over the one that would be restored, and a
`localStorage` that throws in either direction leaves the component
rendering. `viewPersistence.test.tsx` covers the same property for the app as
a whole — every read throwing still renders it, every write throwing still
switches tabs — and `crawlStatusBar.test.tsx` that a corrupt dismissed id
does not bury the banner. Each of those was mutation-checked against the
guard it is about, which is how the `Storage.prototype` trap above was
found. The view test files cover each restored selection through the UI
it belongs to — the request the browser issues on mount carries the stored
artist and sort, the Unmatched dropdown comes back on Unmatched, a stored
sort field the tab no longer offers falls back to Artist, a stored
`discogs_price` sort is dropped under a non-Collection filter and kept under
Collection, and the keys do not leak between scopes.
`viewPersistence.test.tsx` covers the tab: a stored library tab opens lit, an
admin-only or errand value falls back to Collection, and opening Account
leaves the stored tab where it was.
