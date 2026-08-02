# Plex Reachability + SSRF Hardening — Design

**Date:** 2026-08-01

**Amendment (2026-08-02, closing two residual gaps found in final whole-branch
review):** two edge cases in `validate_address` were tracked as follow-up
rather than fixed in the review that produced them (see that review's own
commit message for the full description) — both are now fixed, and the two
sections below ("Reject via `is_global`..." and "SSRF validation") describe
the pre-fix behavior only. (1) **NAT64.** `ipaddress.ip_address(ip).is_global`
does not special-case RFC 6052 NAT64 addresses (`64:ff9b::/96`, which embed an
IPv4 address in the low 32 bits) — `64:ff9b::7f00:1` (NAT64-encoded
127.0.0.1) reads as globally routable. `validate_address` now rejects any
resolved address in `64:ff9b::/96` outright, as one narrow, explicitly
documented exception to "`is_global` only, not a hand-rolled range list" —
this app has no legitimate reason to reach a NAT64-only host, so the simpler
whole-prefix rejection was chosen over decoding and separately validating the
embedded IPv4 address. (2) **Parser divergence.** `validate_address` parsed
`base_url` with `urllib.parse.urlsplit`, while the real outbound request
(`plex.py`'s `_get`) is parsed by `httpx`'s own URL parser when the request
actually fires. Fuzzing found one confirmed divergence: a hostname containing
the Unicode "ideographic full stop" (U+3002) as a label separator —
`urlsplit(...).hostname` keeps it verbatim, `httpx.URL(...).host` normalizes
it to a regular period, so the two parsers could in principle validate and
request different hostnames. No live bypass was demonstrated (every
constructed test case still failed closed), but `validate_address` now parses
`base_url` with `httpx.URL` instead of `urlsplit`, so the hostname it checks
is always exactly the hostname `httpx` will actually resolve for the real
request — same parser, not a second independent one.

**Amendment (2026-08-01, during grounding for the implementation plan):** the
original text below described validating a resolved IP and then connecting
*directly to that IP* (with the hostname preserved only in a `Host` header), to
eliminate the DNS-rebinding TOCTOU gap outright. That breaks `https://` Plex
addresses: TLS certificate validation and the SNI extension both need the
*hostname*, not a raw IP, to work, and Plex's own "Remote Access" reachability
feature — the mechanism this whole plan exists to support — is typically
`https://`. A fully correct fix exists (a custom low-level transport that
resolves/validates the IP itself but still hands the original hostname to the
TLS layer for SNI/cert checks), but it means coding against `httpx`'s internal
`httpcore` transport API rather than its public surface — disproportionate for
an app with a small, invited user base rather than a large public attack
surface. Decided instead: validate immediately before each call, then let
`httpx` connect normally by hostname, so TLS/SNI/cert behavior for `https` is
unaffected. This narrows the DNS-rebinding window to the moment between our
check and httpx's own resolution, rather than eliminating it — an accepted,
explicitly documented residual risk rather than a silently dropped one. The
"Decisions" and "SSRF validation" sections below describe this corrected
design directly, not the original IP-pinning one.

## Overview

This is decomposition item 4 of the multi-tenant migration (see
[`2026-07-26-multi-tenant-architecture-design.md`](2026-07-26-multi-tenant-architecture-design.md)'s
"Plex reachability" section and its Decomposition list). The original single-owner
Plex-match feature ([`2026-07-08-plex-integration-design.md`](2026-07-08-plex-integration-design.md))
was fully removed during the `crawl-queue-refactor` branch's rewrite — `crawl_manager.py`
has no Plex references, the per-user settings endpoints have no Plex fields, and the
frontend has no Plex UI. `backend/plex.py` still exists but is the untouched
single-owner-era client: no per-user awareness, no SSRF protection.

This spec rebuilds the feature per-user, with SSRF hardening designed in from the
start rather than bolted on afterward. The underlying matching behavior (fuzzy
artist/title matching against a cached Plex album list, two-tier link/no-link
UI treatment, recomputed fully on every sync) is unchanged from the original spec —
only the per-user scoping and the network-safety layer are new.

The schema this depends on already shipped in PR #30 (`multi-tenant-architecture-design`):
`users.plex_base_url` / `plex_token` / `plex_match_threshold`, and
`library_items.plex_url` / `plex_matched_at`. No new columns are needed.

## Goals / non-goals

**Goals**
- Every release in a user's collection gets checked against *that user's* Plex
  music library during *that user's* collection sync.
- A confident match links the release's title to the matching Plex album, exactly
  as in the original single-owner feature.
- A user-supplied `plex_base_url` can never be used by the shared backend to probe
  internal network addresses (cloud metadata endpoints, other services on the
  hosting network, loopback) — the SSRF vector introduced by the pivot to
  multi-tenancy.
- The address-safety check cannot be bypassed by an HTTP redirect (a public IP
  that redirects to a private one after the check passes), and is re-run
  immediately before every outbound call rather than once at save time, so a
  hostname that resolves to a public IP when saved but a private IP later
  (DNS rebinding) is caught on the very next sync — see the amendment above
  for why this narrows rather than fully eliminates the rebinding window.

**Non-goals** (carried over from the original spec, and one new one)
- Any UI treatment for a low-confidence or ambiguous match.
- A standalone "resync Plex" action independent of collection sync.
- Surfacing Plex metadata beyond the link itself.
- Matching wishlist releases against Plex — scoped to `in_collection = true` only.
- Multi-library-section support (more than one music library on one Plex server).
- Admin-facing alerting on repeated SSRF-rejection attempts. A quiet skip-and-log
  is enough for v1, consistent with this app's existing "no control surface until
  there's a real need" precedent (e.g. `plex_match_threshold` was config-file-only
  in the original spec for the same reason).

---

## Decisions

**Resolve-and-validate immediately before each call, then let `httpx` connect
normally by hostname.** Pinning the connection to a pre-validated IP (bypassing
`httpx`'s own resolution entirely) would close the DNS-rebinding gap outright,
but it breaks `https://` addresses — TLS certificate validation and SNI both
need the hostname, not a raw IP, and Plex's own "Remote Access" reachability
feature is typically `https://` (see the amendment at the top of this document).
Instead: resolve the configured host ourselves via `socket.getaddrinfo`,
validate every resolved address, and if all are safe, call `httpx.get()`
normally against the original URL — `httpx` performs its own separate
resolution when it actually connects, which narrows this to a small race window
(our check and httpx's own lookup happening back-to-back) rather than the full
"safe at save time, unsafe by the time it's used minutes or days later" gap.
Given this app's realistic threat model — a small number of invited users, not
a large public attack surface — that narrowed window is an acceptable,
explicitly documented trade-off against the complexity of a custom transport.

**Reject via `ipaddress.ip_address(ip).is_global`, not a hand-rolled range list.**
The architecture spec describes the mitigation in terms of specific ranges
(RFC1918, `169.254.0.0/16`, `::1`). Implementing that as a literal list of CIDR
checks is easy to get subtly wrong (IPv4-mapped IPv6 addresses, unique-local IPv6
`fc00::/7`, `0.0.0.0/8`, multicast). Python's `ipaddress` module already
encapsulates most of this correctly. A resolved address is allowed only if
`is_global` is true AND `is_multicast` is false; anything else — private,
loopback, link-local, reserved, multicast, unspecified — is rejected. (Note:
`is_global` alone does not exclude multicast; an explicit `not is_multicast`
check is needed.)

**No redirect-following on any Plex API call.** Per the redirect-handling
decision: `httpx` calls in `plex.py` use `follow_redirects=False`. Any 3xx
response is treated as a request failure (same path as "server unreachable"),
not followed. A real Plex server on a fixed LAN/tunnel address has no legitimate
need to redirect these API calls, so this closes the redirect-based bypass with
no functional cost.

**Validated twice: at save and at every use.** Settings save validates
`plex_base_url` and rejects (400) an unsafe address immediately — better UX than
silently skipping the next sync. The sync path validates again, independently,
immediately before each of the three Plex API calls per sync (`get_music_section_key`,
`fetch_albums`, `get_machine_identifier`) — this is what actually closes the
DNS-rebinding window between save-time and use-time, and between calls within the
same sync. Save-time validation is a UX nicety; use-time validation is the actual
security control and cannot be skipped even though save-time validation exists.

**`plex.py` stays synchronous, matching `discogs.py`'s existing precedent.**
`_sync_collection` already calls `discogs.py`'s plain, synchronous `httpx` functions
directly from inside an `async def` method, blocking the event loop for the
duration of each call — an existing, already-accepted pattern in this codebase's
per-user sync path, not something introduced by Plex. Converting `plex.py` (or
`discogs.py`) to async `httpx` would be a real improvement to a real problem in a
multi-tenant service, but it's an orthogonal architectural change touching a
different module than this plan's stated scope, considered and explicitly
deferred rather than snuck in here.

**`plex_match_threshold` gets a UI field this time.** The original spec kept it
config-file-only, reasoning that a settings-page control wasn't worth it for a
single-owner instance with direct `config.json` access. In the per-user model
there is no config file to hand-edit — the value lives on the `users` row — and
`Account.tsx` already has an established pattern (see `recommendation_item_limit`)
for exactly this kind of tunable numeric setting. Exposing it costs one more table
row in an existing section, not a new abstraction.

---

## SSRF validation

New module `backend/plex_security.py`:

```python
def validate_address(base_url: str) -> None: ...
    # raises PlexUnsafeAddressError if the address is unsafe; returns
    # nothing otherwise -- callers proceed to make their own httpx call
    # against base_url exactly as before, unmodified

class PlexUnsafeAddressError(Exception): ...
```

`validate_address`:
1. Parses `base_url` (defaulting to `http://` if no scheme is present, matching
   `plex.py`'s existing `_base()` helper). Rejects any scheme other than `http`/`https`.
2. Resolves the hostname via `socket.getaddrinfo` (all returned addresses, not just
   the first — a multi-A-record host is unsafe if *any* resolved address is
   non-global, since nothing guarantees which one `httpx`'s own later resolution
   picks).
3. Rejects if any resolved address fails `ipaddress.ip_address(ip).is_global` or is
   multicast (`ipaddress.ip_address(ip).is_multicast`).

`plex.py`'s three functions (`get_music_section_key`, `fetch_albums`,
`get_machine_identifier`) each call `validate_address` immediately before their
existing `httpx.get` call (otherwise unchanged — same hostname-based URL, normal
`httpx`-managed TLS/SNI/cert verification), and add `follow_redirects=False`. A
`PlexUnsafeAddressError` propagates like any other request failure — callers
(`_run_plex_match`, the settings-save validator) treat it as "this address didn't
work," with no distinction surfaced to the caller between "unreachable" and
"unsafe" (see Error handling).

No new dependency — `socket` and `ipaddress` are stdlib.

---

## Sync orchestration

Hooks into `CrawlManager._sync_collection(self, user_id: int, mode: str)` in
`backend/crawl_manager.py`, at the same point the original single-owner version
used: after the wishlist-cleanup block (`cleared = clear_wishlist_flags_not_in(...)`,
`deleted = delete_orphaned_releases(...)`, its `conn.commit()`), before the
`sync_complete` broadcast — still inside the same `user_scope(user_id)` connection
block that's already open for this sync.

1. **Skip if unconfigured.** The `user` row is already fetched near the top of
   `_sync_collection` (for the OAuth-token check) — reuse it rather than a new
   query. If `user["plex_base_url"]` or `user["plex_token"]` is empty, skip
   entirely: no broadcast, no error.
2. **Fetch once per sync.** `plex.get_music_section_key` → `plex.fetch_albums` →
   `plex.get_machine_identifier`, exactly as the original spec, each independently
   SSRF-validated as described above.
3. **Match every `in_collection = true` release for this user** against the cached
   album list, scoped to `user_id` via the same `user_scope` connection —
   `library_items.plex_url` / `plex_matched_at` are per-user columns, so this
   never touches another user's rows even without RLS, but RLS is the actual
   isolation boundary per the architecture spec's existing invariant.
4. **Broadcast**: `plex_match_started`, `plex_match_progress {matched, total}`
   (batched every 25 releases), `plex_match_complete {matched}`,
   `plex_match_error {error}` on failure — same four-event family the original
   plan's deviation note established, extended here with no changes.

No changes to `mode="new"` vs `mode="all"` semantics — unchanged from the original
spec, Plex matching always runs over the user's full current `in_collection` set.

---

## Backend API & Settings

Per-user, not admin — `plex_base_url`, `plex_token`, `plex_match_threshold` join
`UserSettingsUpdate` / `get_user_settings` / `update_user_settings` in
`backend/routers/settings.py`, alongside the existing `anthropic_api_key` /
`recommendation_item_limit`. Not the admin-only `SettingsUpdate` — Plex config is
personal, one server per user, same as Anthropic API key.

- `GET /api/user-settings` gains `plex_base_url: str`, `plex_token: str`,
  `plex_match_threshold: int` (default 90) in its response, read off the `users`
  row already queried.
- `POST /api/user-settings` gains the same three fields on `UserSettingsUpdate`.
  Before saving, runs `plex_security.validate_address(plex_base_url)` if
  `plex_base_url` is non-empty; a `PlexUnsafeAddressError` becomes a 400 with a
  generic message (see Error handling) and the save is rejected — the existing
  fields on the same request are not partially saved.
- **Real gap, not new scope: `db.get_library_releases`'s `SELECT c.* {base_from}`
  selects from `catalog c` only.** `plex_url` / `plex_matched_at` live on
  `library_items li`, so they are currently absent from every `/api/releases`
  response regardless of Plex — the frontend could never show a match even if one
  were written to the database today. Add `li.plex_url, li.plex_matched_at` to
  every `SELECT` branch in `get_library_releases` (the `price_`-sort branch, its
  fallback, and the default branch) and merge them into each returned row.

---

## Frontend

- `frontend/src/api/types.ts`: `Release` gains `plex_url: string | null` and
  `plex_matched_at: string | null`. `UserSettings` (or equivalent) gains
  `plex_base_url`, `plex_token`, `plex_match_threshold`. `CrawlEvent` gains the
  four `plex_match_*` status values and a `matched` field, same shape as the
  original spec.
- `frontend/src/views/Account.tsx`: new "Plex" section, same table/row pattern as
  the existing "Recommendations" section (label cell, input cell, description
  cell) — three rows: server address (text, e.g. a Plex Remote Access
  `plex.direct` address — a LAN address or a private tunnel address such as
  Tailscale is rejected by the SSRF check below, so the copy must not suggest
  either works), token (password-style input), match threshold (number
  input, default 90). One save button for the section, following the existing
  `handleSaveUserSettings` pattern.
- `frontend/src/App.tsx`: SSE handler gains the four `plex_match_*` cases,
  updating the sync status message — same messages and same "rides the existing
  `syncing` toggle, no dedicated one" behavior as the original spec's `App.tsx`
  section.
- `frontend/src/views/RecordBrowser.tsx`: title becomes a conditional hyperlink to
  `plex_url` in both list and tile views — same two-tier treatment (link when
  matched, plain text otherwise) as the original spec. Exact restructuring
  depends on the current file's live structure at implementation time (the file
  has changed since the original spec was written); the implementation plan will
  ground this in the actual current line numbers.

---

## Error handling

Unchanged from the original spec except for one new case:

- **Plex unconfigured**: skipped silently, every sync.
- **Plex unreachable, bad token, or no music section found**: logged server-side,
  phase skipped for that sync, existing `plex_url` values on this user's releases
  are left untouched (not cleared).
- **Address fails SSRF validation** (new): treated identically to "unreachable" at
  use-time — `plex_match_error` is broadcast, existing links are left untouched.
  The error message is a generic `"Plex address not reachable"`, deliberately not
  distinguishing "the DNS lookup failed" from "the address resolved to a private
  range" — a more specific message would let a user iteratively probe internal
  address ranges through the sync path and use the error text as an oracle for
  which ones exist. The backend log (not the broadcast) may record the specific
  `PlexUnsafeAddressError` reason for operator debugging.
- **Settings save with an unsafe address** (new): 400, generic message, save
  rejected outright — this is direct user input at save time, not a background
  process, so immediate rejection is both more useful and lower-risk than
  silently accepting it and failing later.
- **A release matches nothing**: `plex_url` / `plex_matched_at` cleared, same as
  original spec.

---

## Testing

- **`plex_security.validate_address`**: accepts a public IP/hostname; rejects
  loopback (`127.0.0.1`, `::1`), RFC1918 ranges, link-local (`169.254.0.0/16`,
  including the cloud metadata IP `169.254.169.254`), IPv6 unique-local
  (`fc00::/7`), multicast; rejects a non-http(s) scheme; rejects when *any* of several
  resolved addresses for one hostname is non-global, not just the first; rejects
  out-of-range port numbers and malformed hostnames.
- **DNS-rebinding regression**: mock `socket.getaddrinfo` to return a public IP on
  the first call and a private IP on a second call for the same hostname across
  two separate calls to `validate_address` — assert the second (use-time) call is
  independently rejected, proving save-time validation alone would have missed
  it. This proves the mitigation the design actually provides (re-validation on
  every use catches a hostname that changed since it was saved); it does not
  and cannot prove closure of the tighter, sub-request-scoped race the amendment
  above documents as an accepted residual risk.
- **Redirect handling**: a mocked 302 response from the Plex host is treated as a
  failure, not followed — `follow_redirects=False` is asserted via the actual
  request call, not just documented.
- **Settings API**: `POST /api/user-settings` with a private-range `plex_base_url`
  returns 400 and does not persist any field from that request; a public address
  round-trips normally, matching the original spec's settings tests.
- **Sync orchestration**: mirrors the original plan's three `_run_plex_match`
  tests (matched/cleared, no-section-found, connection-failure-leaves-links-untouched),
  adapted to the per-user `library_items` model and scoped so a second user's
  rows are provably untouched by the first user's Plex match run.
- **`get_library_releases`**: a release with a non-null `plex_url` on
  `library_items` is present in the API response under all three sort branches
  (default, `price_<site>` with a matching crawler, `price_<site>` with no
  matching crawler).
- **Frontend hyperlink**: same three cases as the original spec's
  `plexLink.test.tsx` (matched list-view link, unmatched plain text, tile-view
  title-links-to-Plex-while-cover/artist-still-link-to-Discogs), re-grounded in
  the current `RecordBrowser.tsx`.
- **No live-Plex-server test** — mocked throughout, same precedent as the
  original spec and the rest of this codebase's third-party-service tests.

---

## Out of scope

Carried over unchanged from the original spec (low/mid-confidence UI, standalone
resync trigger, wishlist matching, extra Plex metadata beyond the link,
multi-library-section support), plus:

- Converting `plex.py`/`discogs.py` to async `httpx` — see Decisions.
- Admin visibility into repeated SSRF-rejection attempts.
- Any mitigation beyond IP-range/redirect checks (e.g. outbound network
  segmentation, egress proxying) — this is an application-layer control, not a
  substitute for infrastructure-level egress restrictions if the hosting
  environment can support them later.

## Success criteria

- With Plex unconfigured, a user's collection sync behaves exactly as it does
  today — no new calls, no new errors, `plex_url` stays null.
- After a user configures both fields and syncs, their releases matching an album
  in *their* Plex library show a working hyperlink; another user's releases are
  never affected by this user's Plex configuration or sync.
- Pointing `plex_base_url` at a private/loopback/link-local/metadata address is
  rejected at save time, and a hostname that resolves safely at save time but
  unsafely by the next sync is rejected then, on the very next use — not
  silently accepted for the sync's duration.
- A redirect response from the configured address is never followed.
- Removing a previously-matched album from Plex results in the next sync clearing
  that release's link, for that user only.
