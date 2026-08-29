export interface Release {
  discogs_id: string
  artist: string
  title: string
  year: number | null
  label: string
  format: string
  discogs_price: string | null
  cover_image_url: string
  discogs_url: string
  plex_url: string | null
  plex_matched_at: string | null
  last_synced: string
  date_added: string | null
}

export interface ReleasesResponse {
  total: number
  page: number
  per_page: number
  releases: Release[]
}

export type CrawlerGenre = 'marketplace' | 'punk' | 'metal' | 'rock' | 'pop'

export interface Crawler {
  id: number
  site_name: string
  module_path: string
  crawler_type: 'release' | 'catalog' | 'catalog_browser'
  enabled: boolean
  last_run: string | null
  base_url: string | null
  genre_summary?: string | null
  genre: CrawlerGenre
}

export interface Settings {
  crawl_delay_seconds: number
  consecutive_failure_limit: number
  crawl_schedule?: string
  crawl_schedule_mode?: 'missing' | 'all'
  ebay_app_id?: string
  ebay_cert_id?: string
  stock_schedule?: string
}

export interface UserSettings {
  anthropic_api_key: string
  recommendation_item_limit: number
  plex_base_url: string
  plex_token: string
  plex_match_threshold: number
}

export type SortField = 'artist' | 'title' | 'year' | 'label' | 'format' | string
export type SortOrder = 'asc' | 'desc'
export type RecordScope = 'collection' | 'wantlist'
export type StockScope = 'store' | 'track'
export type LibraryScope = 'collection' | 'wantlist' | 'all'

export interface CrawlEvent {
  id?: number
  type?: 'listing_changed'
  status?: 'found' | 'not_found' | 'error' | 'complete' | 'started' | 'stopped' | 'ping'
    | 'sync_started' | 'sync_page_fetched' | 'sync_progress' | 'sync_complete' | 'sync_error'
    | 'stock_sync_started' | 'stock_sync_source_started' | 'stock_sync_page_fetched'
    | 'stock_sync_detail_progress'
    | 'stock_sync_progress' | 'stock_sync_complete' | 'stock_sync_error' | 'stock_sync_aborted'
    | 'stock_judgment_started' | 'stock_judgment_progress' | 'stock_judgment_complete' | 'stock_judgment_error'
    | 'plex_match_started' | 'plex_match_progress' | 'plex_match_complete' | 'plex_match_error'
  discogs_id?: string
  item_key?: string
  release?: string
  artist?: string
  site?: string
  price?: number
  error?: string
  total?: number
  total_pages?: number
  page?: number
  page_count?: number
  synced?: number
  wishlist_synced?: number
  username?: string
  scope?: 'all' | 'wishlist'
  screenshots?: string[]
  source?: string
  sources?: string[]
  judged?: number
  done?: number
  label?: string
  matched?: number
  crawler_id?: number | null
}

export interface CollectionStatus {
  total: number
  last_synced: string | null
}

export interface CrawlStatus {
  total: number
  missing: number
  oldest_checked: string | null
  pending: number
  pool_running: boolean
}

export interface ScreenshotEntry {
  path: string
  url: string
}

export interface ScreenshotSession {
  session_id: string
  entries: ScreenshotEntry[]
}

export type AuthStatus =
  | { state: 'unauthenticated' }
  | { state: 'authenticated'; user: { discogs_username: string; is_admin: boolean } }

export interface Invite {
  code: string
  note: string | null
  created_by_username: string | null
  created_at: string
  redeemed_by_username: string | null
  redeemed_at: string | null
}

export interface StockItem {
  id: number | string
  item_key: string
  artist: string
  title: string
  format: string | null
  price: number | null
  currency: string | null
  url: string
  cover_image_url: string | null
  source: string
  last_seen: string
  reason: string | null
  is_own: boolean
  discogs_price: string | null
  saved: boolean
}

export interface StockResponse {
  total: number
  page: number
  per_page: number
  items: StockItem[]
}

export type StockSortField = 'artist' | 'title' | 'format' | 'price' | 'discogs_price' | 'source'

export interface StockSourceCount {
  crawler_id: number
  site_name: string
  count: number
}

// `total` is the same number StockResponse carries under the same filters, and
// the source counts sum to it -- see the /stock/stats docstring.
export interface StockStats {
  total: number
  sources: StockSourceCount[]
}

export interface RecommendationImportError {
  line: number
  error: string
}

export interface RecommendationImportResult {
  imported: number
  updated: number
  unchanged: number
  skipped: number
  errors: RecommendationImportError[]
  matched_stock_items: number
  running: boolean
}

// Two units, never conflated (see the Queue tab design spec): a *row* is one
// queue target and is what the queue's length and its ETA are denominated in;
// a *work unit* is one (row, crawler) pair -- one search a worker will perform
// -- and is what every per-crawler number counts.
export interface QueueTotals {
  claimable_rows: number
  claimable_release_rows: number
  claimable_stock_rows: number
  held_rows: number
  unactionable_rows: number
  in_progress_rows: number
  stranded_rows: number
  rows_done_last_hour: number
  eta_seconds: number | null
  claimable_units: number
  held_units: number
  in_progress_units: number
}

export interface QueueCrawlerSummary {
  crawler_id: number
  site_name: string
  requires_discogs_release: boolean
  claimable_units: number
  held_units: number
  in_progress_units: number
  // Composition and age cover the crawler's whole pending backlog, claimable
  // and held alike; only claimable_units/held_units split it.
  release_units: number
  stock_units: number
  oldest_wait_seconds: number | null
  age_buckets: { under_1h: number; under_24h: number; over_24h: number }
  results_last_hour: number
  last_result_seconds_ago: number | null
  eta_seconds: number | null
}

export interface QueueSummary {
  totals: QueueTotals
  crawlers: QueueCrawlerSummary[]
  stranded_after_seconds: number
  activity_window_seconds: number
  pool_running: boolean
  generated_at: string
}

export interface QueueNextItem {
  artist: string | null
  title: string | null
  kind: 'release' | 'stock'
  waiting_seconds: number
  narrowed: boolean
}

// One observed price drop on a record the user saved. price/url/previous_best
// are the drop's own copies, not a live lookup: the stock row they came from is
// deleted and reinserted on every catalog sync, so a notification that resolved
// them at read time would silently start describing something else.
export interface PriceDropNotification {
  id: number
  item_key: string
  artist: string
  title: string
  format: string | null
  source: string
  url: string
  price: number
  currency: string | null
  previous_best: number
  cover_image_url: string | null
  created_at: string
}

export interface NotificationsResponse {
  items: PriceDropNotification[]
  unread: number
  latest_id: number | null
  // The read watermark: every item with a greater id is new since the user
  // last opened the tab.
  last_read_id: number
}

export interface NotificationsUnread {
  unread: number
  latest_id: number | null
}
