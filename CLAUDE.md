# Claude Code Instructions — discogs-browser

This repository is specification-driven. The design spec and implementation plan are the authoritative source of truth. Read them before touching any code.

## Essential reading (do this first)

1. [`docs/superpowers/specs/2026-06-27-discogs-browser-design.md`](docs/superpowers/specs/2026-06-27-discogs-browser-design.md) — full architecture, data model, crawler interface, API shapes, UI behaviour
2. [`docs/superpowers/plans/2026-06-27-discogs-browser.md`](docs/superpowers/plans/2026-06-27-discogs-browser.md) — implementation tasks, file-level detail, code examples

## Workspace isolation

**For all development in a session, always work in a git worktree — never just check out or create a branch in the primary checkout.** A branch alone is not isolation: the primary checkout is shared state, so checking one out there (even a fresh feature branch) still risks clobbering whatever the user or another session has checked out. This includes spec/plan edits, not just code. Use the harness's native worktree tool (e.g. `EnterWorktree`) when available; fall back to `git worktree add` (conventionally under `.worktrees/`, gitignored) only when no native tool exists. One worktree per unit of work — branch, implement, commit, and open the PR from there, then remove it.

## Repository layout

```
discogs-browser/
├── backend/               # FastAPI + Playwright Python backend
│   ├── main.py            # app entry point, startup
│   ├── config.py          # CONFIG_DIR, env var overrides, load/save_config
│   ├── crawl_manager.py   # CrawlManager: asyncio background task + SSE fan-out
│   ├── scheduler.py       # APScheduler wrapper for scheduled crawls
│   ├── crawler.py         # plugin loader, crawl_releases() generator, bot recovery
│   ├── db.py              # SQLite schema and all DB helpers
│   ├── discogs.py         # Discogs API client (httpx)
│   ├── screenshots.py     # CrawlScreenshotter
│   ├── crawlers/          # bundled crawler plugins (amazon.py, sgrecordshop.py)
│   ├── routers/           # FastAPI routers (one per domain)
│   ├── scripts/           # dev utilities (capture_fixture.py)
│   └── tests/             # pytest test suite
├── frontend/              # React + Vite + TypeScript + Tailwind SPA
│   ├── src/App.tsx        # root component, SSE connection, nav, modals
│   ├── src/api/           # typed fetch wrappers and TS types
│   └── src/views/         # CollectionBrowser, Settings, LogViewer
├── docker-compose.yml     # two-service Docker deployment
└── docs/                  # spec and plans (read these first)
```

## Key invariants

- **Crawl is a shared queue, not a per-request job.** An in-process worker pool (`CrawlManager.start_worker_pool()`) starts once at app boot and continuously drains `crawl_queue` via `SELECT ... FOR UPDATE SKIP LOCKED`. `POST /api/crawl/start` and collection sync (`_sync_collection`) both just enqueue one row per target for the calling user — the always-running pool picks them up; there's nothing left for either endpoint to "start." `GET /api/crawl/stream` is a persistent SSE connection — it never starts a crawl, only observes. The frontend opens the stream on mount. It is per-user-filtered, but only for events that carry an owner: `_visible_to` (`routers/crawl.py`) gates both the replay buffer and the live loop on a `user_id` tag, which `crawl_manager.py`'s per-job `broadcast` closures stamp onto `sync_*`/`stock_judgment_*`/`plex_match_*`. Everything untagged is global by design and must stay that way — `stock_sync_*` (one shared catalog refresh, no per-user owner) and `listing_changed` (Store/Track are global tabs; filtering it by library ownership starved every user's view once already, fixed in `5e1890e`). See [`docs/specifications/shaping/2026-08-23-per-user-sse-event-filtering-design.md`](docs/specifications/shaping/2026-08-23-per-user-sse-event-filtering-design.md).
- **`crawlers.enabled` is resolved at dispatch, not baked into queue rows.** A `crawl_queue` row names a *target* (`discogs_id` xor `item_key`), never a crawler. `_drain_one_batch` claims target rows and calls `db.get_eligible_crawlers` per row, so enabling or disabling a marketplace crawler takes effect on the next batch with no restart, no purge, and no re-sync. `start_worker_pool` loads plugins for *every* release crawler (`db.get_crawlers`), enabled or not, so a crawler enabled after boot already has its plugin. `pending_crawler_ids` on a row narrows the next pass to crawlers a previous pass deferred behind a circuit-breaker cooldown; `available_at` holds the row out of the claim until the earliest of those cooldowns expires. A stock-item row additionally requires an enabled store to still list its `item_key` in `stock_items` — that gate lives in `claim_crawl_queue_batch` and `enqueue_crawl_queue_for_stock_item`, with `db.delete_dead_stock_crawl_queue_rows` sweeping rows that fail it on disable and at the end of each stock sync. Enabling a marketplace crawler also calls `db.backfill_crawl_queue_for_crawler`, which revives `done` targets it has no priced `listings` row for, narrowed via `pending_crawler_ids` so only the newly enabled crawler runs for them. See [`docs/specifications/shaping/2026-08-14-per-item-crawler-fanout-design.md`](docs/specifications/shaping/2026-08-14-per-item-crawler-fanout-design.md), which supersedes the enqueue-time-selection parts of [`2026-08-09-stop-crawling-disabled-stores-design.md`](docs/specifications/shaping/2026-08-09-stop-crawling-disabled-stores-design.md) and [`2026-08-10-dead-stock-crawl-jobs-design.md`](docs/specifications/shaping/2026-08-10-dead-stock-crawl-jobs-design.md).
- **No listings pre-population.** A `listings` row only exists once a crawl pass actually ran that crawler for that target — no row means "not yet crawled," not a NULL-price placeholder. There's no separate pre-population step; collection sync enqueues real crawl jobs directly.
- **Amazon price extraction is scoped.** All selectors in `extract_price()` are scoped to buybox containers (`#corePrice_feature_div`, `#desktop_buybox`, etc.) to avoid matching carousel/recommendation prices.
- **Playwright channel is configurable.** `PLAYWRIGHT_CHANNEL=""` uses bundled Chromium (Docker). `PLAYWRIGHT_CHANNEL="chrome"` uses the user's real Chrome (local dev default).
- **App authentication is Discogs OAuth 1.0a, gated by invite code for new accounts, always enforced.** `AuthMiddleware` (`backend/auth_middleware.py`) guards every `/api` request via `backend/routers/session.py`. The app is multi-tenant, not single-owner — see [`docs/superpowers/specs/2026-07-26-multi-tenant-architecture-design.md`](docs/superpowers/specs/2026-07-26-multi-tenant-architecture-design.md) and [`docs/superpowers/specs/2026-07-26-discogs-oauth-auth-design.md`](docs/superpowers/specs/2026-07-26-discogs-oauth-auth-design.md). There is no password/TOTP/recovery-code concept to lose access to; losing access to your Discogs account is outside this app's control.
- **Wishlist removal is destructive; collection removal is not.** A release dropped from the Discogs wantlist, and never in the collection, is hard-deleted (row + listings) on the next sync — see `db.delete_orphaned_releases`, called from `crawl_manager._sync_collection`. `in_collection` never auto-clears once set, by design — a release removed from the real Discogs collection is left untouched locally.
- **`bootstrap.sh` must never destroy the Postgres data volume.** It's the routine update/redeploy path (`git pull` + `docker-compose build` + `docker-compose up -d`) and must stay that way — no `docker-compose down -v`, no deleting `postgres-data/`. Any real data reset is a separate, deliberate action a sysadmin runs by hand, never a side effect of a normal re-run.
- **`integration` and `main` are kept in step in *both* directions, and neither is ever pushed to directly.** Dependabot targets `integration` (`.github/dependabot.yml`) so a week of routine bumps costs one deploy instead of one per PR. `.github/workflows/integration-promote.yml` carries that batch into `main`; `.github/workflows/integration-sync.yml` carries `main` back into `integration` (`sync`) and moves stale PR heads forward (`refresh`). Two *separate* failures are being prevented here, and conflating them will send you after the wrong one. **(1) PRs going un-mergeable.** `integration-branch-protection` sets `strict_required_status_checks_policy`, which compares a PR's head with **its own base** — so every open Dependabot PR reports `mergeable_state=behind` the moment *anything* lands on `integration`, whether a bump merging or the sync job itself, and stays there until something moves its head forward. `main` is not an input to that comparison; `integration` trailing `main` does not by itself make any PR `behind`. **(2) Bumps vetted against stale code.** The promotion squash-merges, so it advances `main` and leaves `integration` where it stood; with nothing carrying `main` back, bumps are tested against an ever-staler base and then promoted into a `main` they were never tested against. Both bit at once on 2026-08-25: six Dependabot PRs sat unmergeable, the oldest for a fortnight, against an `integration` that had fallen 64 commits behind `main`. Fixing either alone would have left the other. Both branch rulesets require a pull request, so anything that needs to move either branch opens a PR, including the workflows above. On bypass actors they differ, and the difference is load-bearing: `integration` names none, while `main` names the repository owner with `bypass_mode: pull_request` — still forced through a PR, but able to merge one that has not met the required approving review. That is precisely why the identity those workflows authenticate as matters. The promotion PR is opened by a bot, not a person, so `main`'s required review actually binds; point the workflows at a personal access token owned by the bypass actor and the review requirement silently stops applying to the one PR here that deploys. See the "Why an app rather than a PAT" section of the sync design doc. `integration` accepts **merge commits**, and that is a prerequisite rather than a preference: a sync PR landing as a merge commit is what makes `main`'s tip an ancestor of `integration`, keeping `git merge-base` current by construction. Squash one and the base freezes at that point for good, bringing back the hand-recorded marker protocol that replaced it, and every failure mode documented with it — so `arm_auto_merge` fails loudly rather than falling back to a squash. Two scopes are involved: the repository-level "Allow merge commits" toggle gates the API outright (a 405 otherwise, whatever the rulesets say) and is repo-**wide**, while `allowed_merge_methods` narrows a single branch — `integration`'s should be `["merge"]`, merge **only**. Merge-only rather than merge-permitted because a conflicted sync PR is handed to a person to resolve and merge, and there nothing in the workflow can enforce the method; removing the squash button is the only version that doesn't rely on the resolver remembering. Because the toggle is repo-wide, `main` only stays squash-only if its own ruleset pins `allowed_merge_methods` to squash; verify that rather than assuming it. See [`docs/specifications/shaping/2026-08-25-integration-branch-sync-design.md`](docs/specifications/shaping/2026-08-25-integration-branch-sync-design.md).

## Data directory

App settings, avatars, catalog, listings, logs, and per-user data live in Postgres (see `DATABASE_URL` below). Local filesystem state under `DISCOGS_BROWSER_DATA` (default `~/.discogs-browser/`) is now limited to:

```
~/.discogs-browser/
├── crawlers/            # bundled crawler plugins only — no runtime plugin loading from user-writable paths in the hosted deployment
└── screenshots/         # debug screenshots, YYYYMMDD_HHMMSS/
```

## Running

```bash
# Backend
cd backend && pip install -e ".[dev]" && uvicorn main:app --reload --port 8000

# Frontend
cd frontend && npm install && npm run dev
# → http://localhost:5173

# Tests (Postgres-backed tests need all three vars; see "Tests" below)
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
  IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest
```

## Crawler plugin interface

Each plugin in `backend/crawlers/` (or `~/.discogs-browser/crawlers/`) must implement:

```python
class Crawler:
    site_name: str
    base_url: str
    failure_domain: str  # optional; see below

    @classmethod
    def search_url(cls, release: dict) -> str: ...

    async def search(self, release: dict, page: Page) -> list[dict]:
        # returns [] if not found, or list of:
        # {"url": str, "price": float|None, "shipping": float|None,
        #  "currency": str|None, "condition": str|None}
```

The backend owns the Playwright browser. Plugins receive a live `Page` and must raise `BotDetectedError` on bot interstitials.

Any crawler for a named storefront may additionally declare an optional `genre_summary: str` attribute — a one-sentence description read by Settings to show as a hover tooltip on the store link. Not scoped to the catalog kinds: Settings renders the tooltip for any crawler carrying one, and Waterloo Records is a `release` crawler that declares it.

**`[]` means "the site answered and has nothing." Any failure must raise.** The consecutive-failure circuit breaker cannot tell the two apart otherwise, and on the stock-item path an empty result is deliberately not counted as a failure at all — so a crawler that swallows its errors into `[]` never cools its site off.

`empty_result_is_expected` is optional and release-crawler-only: it declares that an empty `search()` result is a **confirmed miss** rather than possible evidence of breakage, so it records no site-health signal instead of counting toward the breaker. Without it, a run of library releases the store simply does not stock would cool off a perfectly healthy site. Only a literal `True` opts out.

A crawler for a single store earns that guarantee by stocking only a fraction of any library. But that is one way to earn it, not the rule itself. The breaker only has to read emptiness as breakage while a crawler **cannot tell "the site has nothing" from "I could not read the page"**, and a crawler that separates them itself, returning `[]` only for a confirmed miss and raising on the rest, has already given the breaker its signal. `discogs_marketplace` sets it on those grounds despite being a marketplace: it had been answering unparseable pages with `[]`, which cooled the site off *and* — because the write path reads the same `[]` — erased prices it had already found. Setting it on a crawler that has not earned it that way is the failure this guard exists to prevent, so it is the separation, not the attribute, that does the work.

`failure_domain` is optional: crawlers declaring the same value count as one site to that breaker. Only the eBay plugins use it (`"ebay-browse-api"` — one app, one token, one API across more than one `crawlers` row). Omit it and the crawler is its own domain.

## Documentation — never write down a count of things that change

**No document in this repo may state how many crawlers, stores, catalog
sources, sites, plugins, tests, or *files* of any of those kinds exist.** Not in `CLAUDE.md`, not in a spec,
not in a plan, not in a README, not in a commit message or PR description. This
covers exact counts ("34 store crawlers"), approximations ("~40 catalog crawler
plugins"), spelled-out counts ("thirty-three sources ship"), ordinals that imply
a total ("a 34th `catalog`-type source", "the thirty-fifth crawler"), and
running totals ("bringing the total to twenty-six"). Whole-suite test totals
("738 tests") and file-tree annotations ("~100 pytest files") are the same rule.

Several forms hide from a naive `grep` for a number next to a noun, and each of
these slipped through a sweep that looked only for the obvious pattern — check
for them by hand. Note also that `\w+` does not span a hyphen, so a pattern like
`\d+ \w+ plugins` never matches `35 label-store plugins`; allow `[\w-]+`, and
search unwrapped text, since a wrapped line can split the number from its noun:

- **Ordinals fused to the digits** — `32nd catalog source`, `33rd Shopify-based
  source`. `\d+ catalog` doesn't match `32nd catalog`.
- **The number trailing the noun** — `brings the Store tab's total catalog
  sources to eighteen`, `re-crawls all catalog sources (31 as of this writing)`.
  Search for `total .* to`, `as of this writing`, and `\b\d+(st|nd|rd|th)\b`
  as well as the obvious pattern.
- **A generic noun standing in for the inventory** — `all 40 files listed in the
  table below`, `~8 other test files`, `~100 pytest files`. Searching only for
  `crawler|source|site|plugin` misses these, so sweep `files?|fixtures?` too.
- **A spelled-out number**, especially a small one — `the four crawler test
  files`, `six App-rendering test files`. A digit-only pattern never sees them,
  so match `one|two|…|fifty` as well as `\d+`. Do **not** skip the low numbers on
  the theory that they are scoped to one change: filtering out `four` is exactly
  how `the four crawler test files` survived two sweeps.
- **The number hyphenated onto the noun** — `a 40-file edit`, `the ~40-crawler
  list`, `all 30-odd catalog crawler plugins`. There is no space to anchor on,
  and a modifier can sit between the hyphen and the noun, so `\d+ \w+ files` and
  a bare `\d+-(file|crawler)` both miss cases; search `\d+-[\w-]+` followed by an
  inventory noun within a few words.

Why: these numbers change every time a crawler is added, and nothing depends on
them. Every one of them went stale within weeks, and each staleness cost real
work — spec amendments written only to correct a number, then further amendments
correcting the correction, then a plan step instructing the next session to
propagate the new number into three other specs. The count was never load-bearing
for any decision; the churn was pure overhead.

Write it count-free instead:

| Don't | Do |
| --- | --- |
| `~50 bundled plugins` | `bundled plugins` |
| `Thirty-four sources ship: Nuclear Blast, …` | `These sources ship: Nuclear Blast, …` |
| `a 33rd catalog-type source, The Sound Garden` | `another catalog-type source, The Sound Garden` |
| `confirmed against all 31 crawlers` | `confirmed against every crawler` |
| `the 36 catalog crawler plugins` | `every catalog crawler plugin` |
| `a schema column with 31 crawlers writing NULL` | `a schema column with every other crawler writing NULL` |
| `a 32nd catalog source for the Store tab` | `a new catalog source for the Store tab` |
| `brings the total catalog sources to eighteen` | *(delete the clause)* |
| `Total catalog crawlers registered: twenty-two` | *(delete the sentence)* |

Naming the members of a set is fine — an enumerated list of source names carries
real information and updates naturally when a source is added. It's the
*cardinality* that must not be written down. If a reader needs the number, it's
one `ls backend/crawlers/` away.

Scope, so this doesn't over-apply. These stay:

- Configuration values and thresholds — `consecutive_failure_limit`, a
  30-minute cooldown, a 1,000-item cap, port numbers, timeouts.
- Live-data findings from a specific investigation — "3,605 products, 15 pages",
  "58/566 titles mismatch", "all 114 options report `sold_out: false`". These are
  dated observations, not claims about current state.
- Counts scoped to one change and fixed by it — "four new crawlers in this
  batch", "run and confirm all 5 tests pass" for a test file the task just wrote.
- Counts a single sentence defines by enumerating, where the number *is* the
  fact — "`colCount` stays 7 for Track and 6 for Store" describes actual columns
  in actual code. This does not cover a number that merely restates a list
  printed next to it: "read in nine places across five files — a.py:1, b.py:2,
  …" carries nothing the list doesn't, so the count goes and the list stays.
- A count that is a fixed historical fact an argument rests on — "the helper
  wasn't extracted until nine Shopify crawlers had converged on identical
  logic" records when a threshold was crossed; it does not go stale.
- **A test run's own result, reported as verification** in a PR description or
  commit message — "1441 passed, 38 errors". That records what one command did
  on one date; nobody maintains it and no reader takes it as a standing claim.
  What this rule forbids is asserting the suite's *size* ("the suite is 738
  tests"), especially in a spec or README where it reads as current fact. The
  distinction matters because `.github/pull_request_template.md` requires the
  opposite of silence here — "Commands run and their result. 'Tests pass'
  without output is not verification." — so stripping run output from a PR
  description would trade a real verification record for a cosmetic win.

One trap worth calling out: when you de-number a sentence, make sure you don't
change what it claims. "with 34 store crawlers able to enqueue on the order of
20,000 jobs per sync" is an *aggregate* — rewriting it to "with every store
crawler able to enqueue 20,000 jobs" silently multiplies the estimate. Reach for
"collectively", "between them", or "in aggregate" when the count was doing that
work. And read the whole passage, not just the sentence you edited: these
documents often restate the same set a few lines later ("all seven files" after
an enumerated list of seven), and a half-de-numbered passage is worse than an
untouched one.

**If you find yourself about to add or update an inventory count, delete it
instead.** An amendment whose only content is "this count is now N" should not be
written; if you're amending a doc that already carries such a count, remove the
count rather than refreshing it. And do not "restore" a count you notice is
missing from a spec — it is missing on purpose.

## Spec-first workflow

When making significant changes:
1. Update the spec in `docs/superpowers/specs/` first
2. Update the plan in `docs/superpowers/plans/` to add new tasks
3. Implement from the updated plan

For small iterative fixes, updating the spec after the fact is acceptable.

### Plan execution mode

When a written implementation plan (`docs/superpowers/plans/` or `docs/specifications/plans/` — this repo has both trees, see "Pre-PR spec-drift check" below) is ready to execute, always use `superpowers:subagent-driven-development` (fresh subagent per task, review between tasks) without asking which execution mode to use. Don't offer the inline-execution alternative by default — only fall back to it if the user explicitly asks for inline/in-session execution instead.

### Pre-PR spec-drift check (required, every branch)

Before opening a PR — including ad hoc changes that never went through the spec-first steps above — check the diff for drift against every spec, not just the spec for the feature being touched:

1. `grep -rl` across **both** `docs/superpowers/specs/` and `docs/specifications/shaping/` for the files, symbols, section/label names, and UI strings touched by the diff. Specs live in two trees — the older ones under `docs/superpowers/specs/`, the newer under `docs/specifications/shaping/` — and a grep over only one of them under-finds drift.
2. For each match, confirm the spec text still describes what actually shipped on this branch.
3. If any spec has drifted, amend it — with a short note or inline correction, not a full rewrite of history — as its own commit on this branch, and push it before opening or merging the PR. A PR should not merge with known spec drift, even drift it didn't cause but exposed.
4. This applies even when the current change itself has no spec/plan of its own (e.g., a small reorg with no new behavior) — the check is about what the diff broke in other docs, not about whether this change needed a spec.
5. While you're in each spec: if it carries a crawler/store/source/plugin/test **count**, delete the count — see "Documentation — never write down a count of things that change" above. Never update one to a newer number.
6. Note in the PR description what drift was found and fixed (or that none was found).

Plans (`docs/superpowers/plans/` and `docs/specifications/plans/`) are historical per-feature task logs, not living reference — they don't need backporting for this check.

### Pull requests

Always open PRs as ready for review, not as drafts — pass `--draft=false` (or omit `--draft` and don't add `Draft PR` state) whenever creating a PR via `gh`, the GitHub API, or any PR-opening skill. Don't ask which mode to use.

**Never enable auto-merge on a PR you open, and disable it if you find it already enabled** (`gh pr merge <PR> --disable-auto`) before ending your turn. `main`'s branch-protection ruleset now requires 1 approving review before merge — raised from 0 after PR #134 merged without one, closing the race at the platform level so no PR can merge unattended regardless of what triggers merge. But GitHub's own Copilot code review (`copilot_code_review.review_on_push`) still runs asynchronously and is not itself a required reviewer, so the required-approval rule stops an *unattended* merge, it doesn't guarantee whoever approves has actually seen Copilot's comments first — a human can still approve before that review has posted. This was a real, observed failure before the ruleset change: on PR #134 (`required_approving_review_count: 0` at the time), CI going green and auto-merge together let the PR merge in 2 seconds, with Copilot's review posting 20 seconds *after* the merge, too late to matter.

Scope: that rule is about PRs *you* open. It is not a blanket ban on `--auto` in the repo, and in particular it is not a licence to strip `--auto` out of `integration-promote.yml` or `integration-sync.yml` — batching a week of Dependabot bumps into one deploy is the whole point of those workflows, and neither PR they open escapes review. The promotion PR into `main` still waits on `main`'s required approving review; the sync PR only targets `integration`, which requires no approval and carries no Copilot review rule, and only ever replays content already reviewed on its way into `main`.

A single check right after CI passes has the same race shifted one step later: Copilot's review can still land after that check finds nothing, and #134's own review + comments were posted together, after the merge. Checking merely "does *a* review from `copilot-pull-request-reviewer[bot]` exist" is also insufficient once you've pushed more than once: `/pulls/{number}/reviews` returns the PR's full history, so that check is satisfied by a stale review left over from an earlier push while a newer push sits unreviewed. Before considering a PR-opening task finished: capture the head SHA right after your final push (`git rev-parse HEAD`), then **poll** `gh api repos/{owner}/{repo}/pulls/{number}/reviews --paginate` every ~20-30s until a review from `copilot-pull-request-reviewer[bot]` whose `commit_id` matches that exact SHA appears (the reviews endpoint is paginated too — on a long-lived PR with many review rounds, the matching review can land past the first page while an unpaginated poll loops to timeout without ever seeing it), capped at ~5-6 minutes (Copilot's #134 review took ~3.5 minutes end to end) — if it times out, say so explicitly rather than silently treating "no review yet" as "no feedback." Once that review has landed, fetch inline comments with `gh api repos/{owner}/{repo}/pulls/{number}/comments --paginate` (an unpaginated call can silently truncate on a PR with many comments). If a PR merges before this check can complete (auto-merge enabled by something outside your control), treat any legitimate finding the same as if caught pre-merge: fix it and open a small follow-up PR immediately, don't let it slide because the original PR is already closed. For each inline comment you address after the fact, reply on that comment thread (`gh api repos/{owner}/{repo}/pulls/{number}/comments/{comment_id}/replies`) naming the fixing commit, then resolve the thread — but only for comments that actually have a `comment_id` (a "suppressed"/non-inline comment in a review body has none; just fix it, nothing to reply to).

## Tests

- In a Claude Code cloud session, `scripts/cloud-setup.sh` provisions everything the
  suite needs (Postgres, test database, `backend/.env`, backend + Playwright +
  frontend dependencies). The `SessionStart` hook in `.claude/settings.json` does
  not name it directly: it calls `.claude/hooks/session-start.sh`, which refuses
  to provision unless it can establish the working tree is trusted, and hands off
  only then. **That launcher has to stay inside `.claude/`, and the hook has to
  keep going through it.** `anthropics/claude-code-action` restores `.claude/`
  (along with `CLAUDE.md`, `.mcp.json` and a few others) from a pull request's
  *base* branch and leaves everything else at the PR head, and the CLI acts on
  `.claude/settings.json` — hooks included — before any tool-permission gating. A
  hook pointing straight at `scripts/` therefore executes a fork contributor's
  shell with the workflow's write token and `CLAUDE_CODE_OAUTH_TOKEN` in its
  environment. Moving the provisioning script into `.claude/` would *not* fix
  that on its own; it installs `backend/pyproject.toml` and
  `frontend/package.json`, which stay at the PR head and run code at install
  time. The fix is declining to provision at all, from a path a pull request
  cannot edit. `cloud-setup.sh` keeps its own `CLAUDE_CODE_REMOTE` check, but
  that one is a convenience for hand invocation, not the boundary — `scripts/` is
  not restored, so a pull request can delete it. See
  [`docs/specifications/shaping/2026-08-29-session-start-hook-pr-safety-design.md`](docs/specifications/shaping/2026-08-29-session-start-hook-pr-safety-design.md).
  These are the only files under `.claude/` that are not gitignored.
- `pytest-asyncio` with `asyncio_mode = "auto"` (all async tests run automatically)
- HTML fixtures for Amazon price regression tests: `backend/tests/fixtures/crawlers/amazon/`
- To capture a new fixture: `python backend/scripts/capture_fixture.py amazon <url> "Artist - Title"`
- Playwright-dependent code (live crawl, browser launch) is not unit-tested; integration testing is manual
- **A test may never assume pre-existing schema or role state.** The session-scoped `pg_run_database` fixture (`backend/tests/conftest.py`) builds each pytest session a fresh `<base>_run_<hex>` database from `TEMPLATE template0` and poisons `app_user`/`app_identity`'s `BYPASSRLS` attributes (inverted from what `_ensure_role` sets) before the run's first `init_tenant_schema()`. Anything a test asserts on must therefore be constructed by the code under test during that run, not inherited from a prior run or a hand-provisioned local database. See `docs/specifications/shaping/2026-08-09-test-database-freshness-design.md`.
- Poisoning also rotates both roles' passwords to random values; `init_tenant_schema()` rewrites them from `IDENTITY_DB_PASSWORD`/`APP_DB_PASSWORD`. Teardown restores both `BYPASSRLS` bits but *not* the passwords — they are unknowable after the fact, and the next run sets them again. A crashed run can therefore leave the cluster's roles holding random passwords until something re-runs `init_tenant_schema()`; `make test-db-clean` repairs the bits only.
- Sharp edge: roles are cluster-level, not per-database, so two suites running concurrently against one Postgres cluster still interfere at the role level for up to one test (the next `init_tenant_schema()` in each session repairs it). Give each worktree its own Postgres container if running suites in parallel. For the same reason, never run `make test-db-clean` while a suite is in flight — pools close between tests, so a live run's database momentarily has zero backends.
- With `TEST_DATABASE_URL` unset, `pg_run_database` no-ops and only the tests that need a database fail. That set is wider than "tests about Postgres": `tmp_config_dir` now depends on `pg_test_db` (settings live in `app_config`, not a file), so `test_config.py`, `test_logging_config.py`, and the crawler test files requesting it need `TEST_DATABASE_URL` too, whatever they're actually asserting.

## Commits — AI attribution trailers (required, every commit)

Every commit made by an AI agent on this repo must carry AI-attribution git trailers, even a plain `git commit -m` one-liner. The rule originated with a `pr-review-prep` tool whose content-attribution table classified a commit as `Human-attributed` whenever the `ai-generated: true` trailer was absent — a commit with no trailers silently misattributed AI work as human work, no error, no warning. (Found and fixed the hard way: seven commits landed on `metal-catalog-crawlers` with no trailers at all before this rule existed, requiring a `git filter-branch` rewrite + force-push to correct after the fact.) That tool is no longer installed, but the trailers remain required — they're the only record of provenance in this repo's history.

Required trailer block, appended as the last paragraph of the commit message (blank line before it):

```
Note: This commit message was created by AI
ai-generated: true
ai-model: <actual model identifier for this session>
ai-tool: <actual tool — see known values below; introduce a new value rather than mislabeling as an existing one if none fits>
ai-surface: <actual surface, same rule>
ai-executor: local-agent | remote-agent — local-agent only when the agent process is verified to run alongside the developer's own machine; when in doubt (e.g. a generic sandboxed path like `/home/agent/...` rather than the developer's real home directory), use remote-agent rather than assuming local
```

Known values, in kebab-case, as used on this repo's history:

- `ai-tool`: `claude-code`, `claude-agent-sdk`
- `ai-surface`: `claude-code-cli`, `claude-code-desktop`, `claude-code-web` (Claude Code on the web / a remote agent session), `fleetview`
- `ai-model`: the exact model id (e.g. `claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5`), never a family name

**`ai-model` stays required even when the running agent's own harness forbids
writing a model identifier into a repository.** Some Claude Code surfaces — the
remote/web one among them — carry a session instruction against putting a model
identifier in commit messages, PR bodies, code comments, or anything else
pushed to a repo, on the grounds that the serving model can differ from the
configured one and a guessed name would be wrong. That reasoning does not reach
this trailer: it records provenance rather than asserting an identity, and the
value is knowable rather than guessed — a remote session reads it from its own
session metadata, via the claude-code-remote `get_session` tool with
`session_id` omitted. Two fields there can disagree, and
`external_metadata.last_served_model` is the one to write: it names the model
that actually ran the turn, including a turn-scoped fallback (overload,
model unavailable) that never reaches `session_context.model`, which is only
what the session is *configured* to run. The trailer records what did the
work, so the served model wins wherever the two differ; use
`session_context.model` only when no served model is reported at all.

Dropping the line leaves the provenance record this whole section exists for
missing the one field that says *which* model did the work — and it fails
silently, exactly like the absent `ai-generated: true` that the rule started
with. Decided 2026-08-28, after a remote session omitted it on four commits
that had already merged. Those are left as they stand: they are on `main`, and
a rewrite there costs a force-push to a protected branch —
the same `filter-branch` correction this section already records as the
expensive way to fix trailers after the fact.

Older commits carry unnormalized variants (`Claude Code`, `Claude Agent SDK`, bare `cli`). Match the kebab-case forms above for new commits; don't rewrite history to match.

Create the commit via `git commit -F <message-file>`, not `git commit -m` — trailers are easy to drop with `-m` due to shell quoting, and `-F` makes them mechanically part of the message.

## Versioning

`backend/version.py`'s `VERSION` is **derived, never edited**. A PR that changes a version number is wrong by definition; there is nothing to bump.

The value is `YYYY.MM.DD+<short-sha>` (e.g. `2026.08.10+8fac644`), resolved at import from, in order: the `APP_VERSION` environment variable, then git, then `"dev"`. CI bakes the real value into the Fly image as a Docker build argument.

The old scheme required each PR to bump a shared literal before merge, but whether the number was right could only be known at merge — so concurrent PRs collided routinely (see `docs/specifications/shaping/2026-08-10-derived-version-design.md`). There are no major/minor components any more, and no `3.x` successor.

## Style notes

- No inventory counts in prose — see "Documentation — never write down a count of things that change"
- No comments unless the WHY is non-obvious
- No backwards-compat shims — just change the code
- Python ≥3.9 (no `str | None` syntax — use `Optional[str]` or untyped)
- Prefer editing existing files; don't create new abstractions without a clear reason
