# Store Genre Summaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a one-sentence genre summary as a hover tooltip on each catalog-crawler store's link in the Settings → Store Management table, so a user can tell what a store sells without leaving the page.

**Architecture:** `genre_summary` is a new class attribute on catalog-crawler plugins (`backend/crawlers/*.py`), read the same way `base_url` already is — dynamically imported per-request in `db.get_all_crawlers()`, with a `None` fallback on import failure. It flows through the existing `/api/crawlers` response and the frontend `Crawler` type into a native HTML `title` attribute on the store link, matching this codebase's existing tooltip convention (no custom tooltip component).

**Tech Stack:** Python (FastAPI, no new deps), TypeScript/React (Vite, no new deps).

## Global Constraints

- Scope is catalog/catalog_browser crawlers only (Store Management / Store Catalog Sources table). Release crawlers (Amazon, eBay, eBay/CCmusic, Discogs Marketplace) get no `genre_summary` and no tooltip.
- No DB migration, no new API endpoint, no admin edit UI — `genre_summary` is plugin-file content, same as `base_url`.
- No comments unless the WHY is non-obvious (repo style).
- Python ≥3.9 syntax (no `str | None`, use `Optional[str]` or leave untyped).

---

### Task 1: Backend — `get_all_crawlers` reads `genre_summary`

**Files:**
- Modify: `backend/db.py:793-812` (`get_all_crawlers`)
- Test: `backend/tests/test_crawler_crud.py`

**Interfaces:**
- Produces: every dict `db.get_all_crawlers(conn)` returns now has key `"genre_summary"` (`str | None`), populated from `getattr(mod.Crawler, "genre_summary", None)` inside the existing dynamic-import try block, and `None` on the existing except-fallback branch.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_crawler_crud.py`:

```python
def test_get_all_crawlers_reads_genre_summary(admin_conn, tmp_path):
    crawler_file = tmp_path / "genre_test_crawler.py"
    crawler_file.write_text(
        "class Crawler:\n"
        "    site_name = 'Genre Test Store'\n"
        "    base_url = 'https://example.com'\n"
        "    genre_summary = 'Sells only kazoo solos.'\n"
    )
    db.register_crawler(admin_conn, "Genre Test Store", str(crawler_file), crawler_type="catalog")
    admin_conn.commit()

    crawlers = db.get_all_crawlers(admin_conn)
    row = next(c for c in crawlers if c["site_name"] == "Genre Test Store")
    assert row["genre_summary"] == "Sells only kazoo solos."


def test_get_all_crawlers_genre_summary_defaults_to_none(admin_conn, tmp_path):
    crawler_file = tmp_path / "no_genre_test_crawler.py"
    crawler_file.write_text(
        "class Crawler:\n"
        "    site_name = 'No Genre Test Store'\n"
        "    base_url = 'https://example.com'\n"
    )
    db.register_crawler(admin_conn, "No Genre Test Store", str(crawler_file))
    admin_conn.commit()

    crawlers = db.get_all_crawlers(admin_conn)
    row = next(c for c in crawlers if c["site_name"] == "No Genre Test Store")
    assert row["genre_summary"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_crawler_crud.py -k genre_summary -v`

Expected: FAIL with `KeyError: 'genre_summary'` on both tests.

- [ ] **Step 3: Implement**

In `backend/db.py`, replace `get_all_crawlers`:

```python
def get_all_crawlers(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM crawlers ORDER BY site_name").fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("_tmp", d["module_path"])
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            d["base_url"] = getattr(mod.Crawler, "base_url", None)
            d["genre_summary"] = getattr(mod.Crawler, "genre_summary", None)
        except Exception as e:
            # base_url/genre_summary are cosmetic here (they only feed the
            # crawler list), so a broken plugin must not fail the whole
            # listing -- but stay consistent with crawler.py's loader and
            # leave a trace rather than silently reporting None for a
            # plugin that won't import.
            log.warning("Could not load crawler plugin %s for base_url: %s", d["module_path"], e)
            d["base_url"] = None
            d["genre_summary"] = None
        result.append(d)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_crawler_crud.py -v`

Expected: PASS, all tests in the file including the two new ones.

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_crawler_crud.py
git commit -F - <<'EOF'
Read genre_summary from crawler plugins in get_all_crawlers

Why: mirrors base_url -- a class attribute read live per-request, no
migration -- so Store Management can show a hover tooltip describing
what each store sells.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

### Task 2: Backend — populate `genre_summary` on every catalog crawler plugin

**Files:**
- Modify: all files listed in the table below, each `backend/crawlers/<file>.py`
- Test: `backend/tests/test_main.py`

**Interfaces:**
- Consumes: `db.get_all_crawlers` from Task 1 (already reads `genre_summary`).
- Produces: every catalog/catalog_browser crawler plugin has a non-empty `genre_summary: str` class attribute; release-type plugins (`amazon.py`, `ebay.py`, `ebay_general.py`, `discogs_marketplace.py`) are untouched and stay `None`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_main.py` (after `test_startup_seeds_bundled_crawlers`):

```python
def test_startup_seeds_catalog_crawlers_with_genre_summary(pg_test_db):
    with patch("main.crawl_manager.start_worker_pool", new=AsyncMock()), \
         patch("main.crawl_manager.stop_worker_pool", new=AsyncMock()):
        import main
        with TestClient(main.app):
            with db.get_admin_pool().connection() as conn:
                crawlers = db.get_all_crawlers(conn)

    assert len(crawlers) == len(sorted(main.BUNDLED_CRAWLERS_DIR.glob("*.py")))
    catalog_crawlers = [c for c in crawlers if c["crawler_type"] in ("catalog", "catalog_browser")]
    release_crawlers = [c for c in crawlers if c["crawler_type"] == "release"]
    assert catalog_crawlers
    assert release_crawlers

    missing = [c["site_name"] for c in catalog_crawlers if not c["genre_summary"]]
    assert missing == [], f"catalog crawlers missing genre_summary: {missing}"
    assert all(c["genre_summary"] is None for c in release_crawlers)

    century_media = next(c for c in catalog_crawlers if c["site_name"] == "Century Media")
    assert century_media["genre_summary"] == "Metal label spanning death, black, and gothic metal."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_main.py -k genre_summary -v`

Expected: FAIL — `missing` assertion lists every catalog site name.

- [ ] **Step 3: Add `genre_summary` to each plugin**

For each file below, insert a `genre_summary: str = "..."` line directly after the existing `base_url: str = "..."` line (before `crawler_type`). Example for `centurymedia.py` (line 10 is the existing `base_url` line):

```python
    site_name: str = "Century Media"
    base_url: str = "https://centurymedia.store"
    genre_summary: str = "Metal label spanning death, black, and gothic metal."
    crawler_type: str = "catalog"
```

Apply the same insertion (after `base_url`, before `crawler_type`) to every file, with this text:

| File | `genre_summary` text |
|---|---|
| `amoeba.py` | `Large independent record store selling new and used vinyl and CDs across nearly every genre.` |
| `angryyoungandpoor.py` | `Independent record store and distro focused on punk and hardcore.` |
| `bigscarymonstersusa.py` | `Label specializing in emo, post-hardcore, and math rock.` |
| `centurymedia.py` | `Metal label spanning death, black, and gothic metal.` |
| `cleorecs.py` | `Reissue label spanning goth, industrial, new wave, and classic rock.` |
| `closedcasketactivities.py` | `Hardcore and metalcore label.` |
| `craftrecordings.py` | `Reissue label for jazz, soul, blues, and classic rock catalogs.` |
| `deathwishinc.py` | `Hardcore and heavy underground label.` |
| `epitaph.py` | `Punk rock label.` |
| `equalvision.py` | `Punk, hardcore, and emo label.` |
| `fatherdaughterrecords.py` | `Indie rock and indie pop label.` |
| `fatpossum.py` | `Blues, garage rock, and Southern indie rock label.` |
| `fatwreck.py` | `Melodic punk label.` |
| `fearlessrecords.py` | `Pop-punk, emo, and alternative rock label.` |
| `flatspotrecords.py` | `Hardcore and metalcore label/store rooted in skate culture.` |
| `jadetree.py` | `Emo and indie rock label.` |
| `killrockstars.py` | `Indie rock and riot grrrl label.` |
| `napalmrecords.py` | `Power, folk, gothic, and symphonic metal label.` |
| `nuclearblast.py` | `Major extreme and heavy metal label.` |
| `numerogroup.py` | `Reissue label for obscure soul, funk, gospel, and outsider music.` |
| `peaceville.py` | `Doom, death, and black metal label.` |
| `piratespressrecords.py` | `Punk, oi!, and rockabilly label and pressing plant.` |
| `polyvinylrecords.py` | `Indie rock and indie pop label.` |
| `prostheticrecords.py` | `Extreme and progressive metal label.` |
| `relapse.py` | `Extreme metal and grindcore label.` |
| `revhq.py` | `Hardcore punk label/store (Revelation Records).` |
| `riserecords.py` | `Post-hardcore, metalcore, and pop-punk label.` |
| `runforcoverrecords.py` | `Indie emo and pop-punk label.` |
| `saddlecreek.py` | `Indie rock and folk label.` |
| `seasonofmist.py` | `Extreme metal label.` |
| `secretlystore.py` | `Indie rock and singer-songwriter label group (Secretly Canadian / Jagjaguwar / Dead Oceans).` |
| `sgrecordshop.py` | `Independent record store with a broad new and used selection across genres.` |
| `subpopmegamart.py` | `Grunge-rooted indie rock label store.` |
| `temporaryresidence.py` | `Post-rock, ambient, and experimental label.` |
| `triplebrecords.py` | `Hardcore label.` |
| `twentybuckspin.py` | `Doom, sludge, and death metal label.` |

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_main.py -v`

Expected: PASS, all tests in the file including the new one.

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest`

Expected: PASS (no regressions in per-crawler fixture tests — `genre_summary` is an added attribute, not a behavior change to `crawl_catalog`/`search`).

- [ ] **Step 6: Commit**

```bash
git add backend/crawlers/*.py backend/tests/test_main.py
git commit -F - <<'EOF'
Add genre_summary to every catalog crawler plugin

Why: populates the data Task 1 wired up, so Store Management can
show a one-sentence description of what each store sells.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

### Task 3: Frontend — genre summary tooltip on the store link

**Files:**
- Modify: `frontend/src/api/types.ts:24-32` (`Crawler` interface)
- Modify: `frontend/src/views/Settings.tsx:166-171` (store link cell)
- Modify: `frontend/src/test/settings.test.tsx`

**Interfaces:**
- Consumes: `Crawler.genre_summary` from the backend (Tasks 1–2).
- Produces: `Crawler.genre_summary?: string | null` — declared **optional** (not required like `base_url`) specifically so the other test files that construct `Crawler` literals (`staleSignupLink.test.tsx`, `viewRenderChurn.test.tsx`, `wantlistRefresh.test.tsx`, `accountNav.test.tsx`, `inStockTab.test.tsx`, `account.test.tsx`, `crawlStatusBar.test.tsx`, `client.test.ts`) don't need touching — they never assert on `genre_summary` and `undefined` behaves identically to `null` at the one call site (`c.genre_summary ?? undefined`).

- [ ] **Step 1: Write the failing test**

In `frontend/src/test/settings.test.tsx`, change the `Epitaph` entry in `CRAWLERS` (line 30) to carry a `genre_summary` and a `base_url` so it renders as a link:

```typescript
  { id: 3, site_name: 'Epitaph', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: 'https://www.epitaph.com', genre_summary: 'Punk rock label.' },
```

Add a new test in the `describe('Settings', ...)` block:

```typescript
  it('shows the genre summary as a hover tooltip on the store link', async () => {
    renderSettings({ crawlers: CRAWLERS })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    expect(screen.getByTitle('Punk rock label.')).toHaveTextContent('Epitaph')
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- settings.test.tsx -t "hover tooltip"`

Expected: FAIL — `Punk rock label.` title not found; also a TS error on `genre_summary` not existing on `Crawler`.

- [ ] **Step 3: Implement**

In `frontend/src/api/types.ts`, add the field to `Crawler`:

```typescript
export interface Crawler {
  id: number
  site_name: string
  module_path: string
  crawler_type: 'release' | 'catalog' | 'catalog_browser'
  enabled: boolean
  last_run: string | null
  base_url: string | null
  genre_summary?: string | null
}
```

In `frontend/src/views/Settings.tsx`, replace the store-link cell inside `renderCrawlerTable`:

```typescript
              <td className="py-3 pr-4 text-left text-gray-200 font-medium">
                {c.base_url
                  ? <a href={c.base_url} target="_blank" rel="noreferrer"
                       title={c.genre_summary ?? undefined}
                       className="text-gray-400 hover:text-white underline">{c.site_name}</a>
                  : <span title={c.genre_summary ?? undefined}>{c.site_name}</span>}
              </td>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- settings.test.tsx`

Expected: PASS, all tests in the file.

- [ ] **Step 5: Add `genre_summary: null` to the other three `Crawler` literals in this file for clarity**

These don't strictly need it (the field is optional), but the file is small and consistent literals read better. Update lines that still read `base_url: null },` in `settings.test.tsx` (the `Amazon`, `Disabled Site` entries in `CRAWLERS`, the `Disabled Catalog` entry in `CATALOG_CRAWLERS_WITH_DISABLED`, and the inline `Angry Young and Poor` entry in the `catalog_browser` bucketing test) to also read `genre_summary: null },`.

- [ ] **Step 6: Run the full frontend suite**

Run: `cd frontend && npm test`

Expected: PASS, no regressions.

- [ ] **Step 7: Manual verification**

Start the backend and frontend dev servers, open Settings as an admin, hover over a catalog crawler's store name (e.g. Epitaph), confirm the browser's native tooltip shows the genre summary after the standard hover delay. Confirm Amazon/eBay/Discogs Marketplace show no tooltip.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/views/Settings.tsx frontend/src/test/settings.test.tsx
git commit -F - <<'EOF'
Show store genre summary as a hover tooltip in Store Management

Why: lets a user tell what a store sells before deciding to hide it,
without leaving the Settings page.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
```

---

## Post-implementation

- [ ] Run the pre-PR spec-drift check (per `CLAUDE.md`): grep `docs/superpowers/specs/` and `docs/specifications/shaping/` for `base_url`, `crawler_type`, `Store Management`, `genre_summary` to confirm no other spec describes the store-link cell or the `Crawler` type in a way this change contradicts.
- [ ] Open PR (ready for review, not draft) noting the spec-drift check result.
