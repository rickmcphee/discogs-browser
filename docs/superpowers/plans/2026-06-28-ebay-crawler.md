---
title: eBay Browse API Crawler — CC Music
status: active
origin: docs/brainstorms/2026-06-28-ebay-crawler-requirements.md
branch: dev-ebay-crawler
created: 2026-06-28
---

# eBay Browse API Crawler — CC Music

Replace the Playwright-based CC Music crawler with a pure `httpx` eBay Browse API crawler. The site name stays `"CC Music"` so the collection view column and existing listing records are preserved.

---

## Problem Frame

`backend/crawlers/ccmusic.py` scrapes ccmusic.com directly via Playwright. The site is Cloudflare-protected; bot detection causes frequent failures. CC Music's eBay storefront (`collectorschoicemusic`) is accessible via the eBay Browse API — structured, authenticated, no bot risk.

(see origin: `docs/brainstorms/2026-06-28-ebay-crawler-requirements.md`)

---

## Scope

**In:** new `backend/crawlers/ebay.py` crawler plugin; eBay API key field in Settings (backend + frontend); token caching with auto-refresh; removal of `ccmusic.py` after verification.

**Out:** general eBay search across all sellers; auction/best-offer listing types; multiple condition tiers; other eBay sellers.

---

## Decisions

### Plugin file name is `ebay.py`, not `ccmusic.py`

`ccmusic.py` is removed. The new file at `backend/crawlers/ebay.py` sets `site_name = "CC Music"`. DB key is `site_name`, so the `crawlers` table row is preserved — `register_crawler` is idempotent on `site_name`, not file path. The old crawler file in `workspace/crawlers/` is also removed.

**Risk:** if the NAS `workspace/crawlers/ccmusic.py` persists after deploy it will be re-synced to the data dir and re-registered, creating a duplicate `"CC Music"` row. Mitigate in `seed_bundled_crawlers` by deleting `workspace/crawlers/ccmusic.py` from the data dir if it exists but is no longer in `BUNDLED_CRAWLERS_DIR`. Alternatively: deploy instructions note this step. The registration is idempotent by `site_name`, so the old file would just overwrite the path column; the simplest mitigation is a delete in `seed_bundled_crawlers` or the deploy script.

### Token caching at module level

eBay application tokens expire after 7200 s. The crawler fetches a new token via client credentials OAuth and caches it at module level with the `expires_at` timestamp. Any `search()` call checks freshness before the API request; if stale, re-fetches. This avoids per-request token round-trips and survives multiple crawl cycles without restart.

Config provides `ebay_app_id` (Client ID) and `ebay_cert_id` (Client Secret). Both required; missing either logs a warning and returns `[]` (same pattern as `discogs_token`).

### Seller ID stored as a module constant

`CCMUSIC_SELLER = "collectorschoicemusic"` — confirmed against live eBay storefront. Not exposed as a config field (out of scope per requirements).

### Buy It Now only, lowest price

The Browse API `filter` parameter accepts `buyingOptions:{FIXED_PRICE}` to exclude auctions. Results are sorted by `price+shippingCost` asc (`sort=price+shippingCost`). Take `itemSummaries[0]`. If `itemSummaries` is absent or empty, return `[]`.

### `search_url()` returns eBay store search URL

Format: `https://www.ebay.com/sch/collectorschoicemusic/i.html?_nkw=<artist+title>`. This is the pre-populated link shown in the UI before a real crawl runs.

### `login_url` set to `""`

No browser session needed. Setting `login_url = ""` suppresses the Site Sessions section in the Crawlers UI.

---

## Implementation Units

### 1. `backend/crawlers/ebay.py` (new)

**Interface:**
```
class Crawler:
    site_name: str = "CC Music"
    base_url: str = "https://www.ebay.com/str/collectorschoicemusic"
    login_url: str = ""

    @classmethod
    def search_url(cls, release: dict) -> str: ...

    async def search(self, release: dict, page) -> list[dict]: ...
```

**Token management** (module-level):
- `_token` (untyped for Python 3.9 compatibility), `_token_expires_at: float` (epoch seconds)
- `async def _get_token(app_id, cert_id) -> str` — POSTs to `https://api.ebay.com/identity/v1/oauth2/token` with `grant_type=client_credentials` and scope `https://api.ebay.com/oauth/api_scope`. Caches result; re-fetches if within 60 s of expiry.

**`search()` logic:**
1. Load config; extract `ebay_app_id` and `ebay_cert_id`. If either missing, log warning, return `[]`.
2. Call `_get_token(app_id, cert_id)`.
3. Build query: `f"{release['artist']} {release['title']}"`.
4. GET `https://api.ebay.com/buy/browse/v1/item_summary/search` with:
   - `q=<query>`
   - `filter=sellers:{collectorschoicemusic},buyingOptions:{FIXED_PRICE}`
   - `sort=price+shippingCost`
   - `limit=3` (to give result validation multiple candidates)
   - `Authorization: Bearer <token>`
5. Parse `itemSummaries[0]`. Extract:
   - `url` → `item["itemWebUrl"]`
   - `price` → `float(item["price"]["value"])`
   - `shipping` → `float(item["shippingOptions"][0]["shippingCost"]["value"])` if present, else `None`
   - `currency` → `item["price"]["currency"]`
   - `condition` → `item.get("condition")`
6. Return `[listing_dict]` or `[]`.

**Error handling:** `httpx.HTTPStatusError` and `httpx.RequestError` are caught, logged, and return `[]`. Token fetch errors propagate as exceptions so `crawl_releases` records them as failures.

**Test file:** `backend/tests/test_ebay_crawler.py`

---

### 2. `backend/routers/settings.py`

Add `ebay_app_id: str = ""` and `ebay_cert_id: str = ""` to `SettingsUpdate`. Add both keys to `get_settings()` response and `update_settings()` save block.

**No new test file** — the settings router has no dedicated test; the pattern is covered by existing integration behavior. A smoke assertion in `test_ebay_crawler.py` that `load_config()` round-trips both keys is sufficient.

---

### 3. `frontend/src/api/types.ts`

Add `ebay_app_id: string` and `ebay_cert_id: string` to the `Settings` interface.

---

### 4. `frontend/src/views/Settings.tsx`

Add two entries to `SETTING_ROWS`:
```
{ key: 'ebay_app_id',  label: 'eBay App ID',   type: 'password', description: 'eBay Client ID (App ID) for Browse API access.' }
{ key: 'ebay_cert_id', label: 'eBay Cert ID',   type: 'password', description: 'eBay Client Secret (Cert ID) for Browse API access.' }
```
Add `ebay_app_id: ''` and `ebay_cert_id: ''` to the `useState` initializer.

---

### 5. Remove `backend/crawlers/ccmusic.py`

Delete file. Add a guard in `seed_bundled_crawlers` (`main.py`) to remove stale `ccmusic.py` from `CRAWLERS_DIR` if it exists there but not in `BUNDLED_CRAWLERS_DIR`, preventing ghost re-registration on NAS restart.

---

## Test Scenarios — `backend/tests/test_ebay_crawler.py`

All use `respx` for httpx mocking. `asyncio_mode = "auto"` is set in `pyproject.toml` so tests are plain `async def`.

1. **`test_search_returns_lowest_price_listing`** — mock token endpoint + search endpoint returning one `itemSummaries` entry; assert returned dict has correct `price`, `shipping`, `currency`, `condition`, `url`.

2. **`test_search_returns_empty_when_no_results`** — mock search returning `{}` (no `itemSummaries` key); assert `[]`.

3. **`test_search_returns_empty_when_missing_config`** — call `search()` without `ebay_app_id`/`ebay_cert_id` in config; assert `[]` and no HTTP calls made.

4. **`test_search_returns_empty_on_http_error`** — mock search endpoint returning 403; assert `[]` (no raise).

5. **`test_token_is_cached`** — call `search()` twice; assert token endpoint called only once (respx call count).

6. **`test_token_refreshed_when_expired`** — set `_token_expires_at` to past value; call `search()`; assert token endpoint called again.

7. **`test_search_url_format`** — call `Crawler.search_url({"artist": "Miles Davis", "title": "Kind of Blue", "format": "Vinyl"})`; assert URL contains `collectorschoicemusic` and URL-encoded artist+title.

8. ~~**`test_prepopulate_with_ebay_crawler`**~~ — removed; `prepopulate_listings` was deleted in v1.45.

9. **`test_config_round_trip`** (settings smoke) — save config with `ebay_app_id` + `ebay_cert_id`, reload; assert values preserved.

---

## Dependencies

- `httpx` already in `backend/pyproject.toml` (used by `discogs.py`).
- `respx` already in test deps.
- No new runtime dependencies.

---

## Sequencing

1. Write `ebay.py` with token fetch + search (unit-testable independently of DB).
2. Write tests in `test_ebay_crawler.py` — verify all 9 scenarios pass.
3. Update `settings.py` backend + `types.ts` + `Settings.tsx` frontend.
4. Add stale-file guard in `main.py` `seed_bundled_crawlers`.
5. Delete `backend/crawlers/ccmusic.py`.
6. Manual smoke: add eBay keys in Settings UI, run Refresh Prices on a known release, verify CC Music column populates.
7. Commit and push to `dev-ebay-crawler`.

---

## Risks

| Risk | Mitigation |
|---|---|
| Stale `ccmusic.py` on NAS data dir causes ghost re-registration | Guard in `seed_bundled_crawlers` to delete stale files |
| eBay API returns `warning` in response but still works (observed in testing) | Treat non-empty `itemSummaries` as success regardless of `warnings` key |
| Token expiry mid-crawl (7200 s TTL) | 60 s expiry buffer in cache check |
| Rate limit (5000/day) | ~600-release collection = ~600 calls/full crawl; well within limit |
