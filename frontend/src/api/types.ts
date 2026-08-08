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

export interface Crawler {
  id: number
  site_name: string
  module_path: string
  crawler_type: 'release' | 'catalog' | 'catalog_browser'
  enabled: boolean
  last_run: string | null
  base_url: string | null
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
export type RecordScope = 'discogs' | 'wishlist'

export interface CrawlEvent {
  id?: number
  status?: 'found' | 'not_found' | 'error' | 'complete' | 'started' | 'stopped' | 'ping'
    | 'sync_started' | 'sync_page_fetched' | 'sync_progress' | 'sync_complete' | 'sync_error'
    | 'stock_sync_started' | 'stock_sync_progress' | 'stock_sync_complete' | 'stock_sync_error' | 'stock_sync_aborted'
    | 'stock_judgment_started' | 'stock_judgment_progress' | 'stock_judgment_complete' | 'stock_judgment_error'
    | 'plex_match_started' | 'plex_match_progress' | 'plex_match_complete' | 'plex_match_error'
  discogs_id?: string
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

export interface StockItem {
  id: number
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
}

export interface StockResponse {
  total: number
  page: number
  per_page: number
  items: StockItem[]
}

export type StockSortField = 'artist' | 'title' | 'format' | 'price'
