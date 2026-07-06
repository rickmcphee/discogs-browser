export interface Listing {
  url: string
  price: number | null
  shipping: number | null
  currency: string | null
  condition: string | null
  last_checked: string
}

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
  last_synced: string
  listings: Record<string, Listing | null>
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
  crawler_type: 'release' | 'catalog'
  enabled: boolean
  last_run: string | null
  base_url: string | null
  login_url: string | null
}

export interface Settings {
  discogs_token: string
  debug_screenshot_interval: number
  shuffle_crawl_order: boolean
  crawl_delay_seconds: number
  consecutive_failure_limit: number
  crawl_schedule?: string
  crawl_schedule_mode?: 'missing' | 'all'
  collection_schedule?: string
  collection_schedule_mode?: 'all' | 'new'
  ebay_app_id?: string
  ebay_cert_id?: string
  stock_schedule?: string
  anthropic_api_key?: string
}

export type SortField = 'artist' | 'title' | 'year' | 'label' | 'format' | string
export type SortOrder = 'asc' | 'desc'
export type RecordScope = 'collection' | 'wishlist'

export interface CrawlEvent {
  status?: 'found' | 'not_found' | 'error' | 'complete' | 'started' | 'stopped' | 'ping'
    | 'sync_started' | 'sync_progress' | 'sync_complete' | 'sync_error'
    | 'stock_sync_started' | 'stock_sync_progress' | 'stock_sync_complete' | 'stock_sync_error'
    | 'stock_judgment_started' | 'stock_judgment_progress' | 'stock_judgment_complete' | 'stock_judgment_error'
  discogs_id?: string
  release?: string
  artist?: string
  site?: string
  price?: number
  error?: string
  total?: number
  total_pages?: number
  page?: number
  synced?: number
  wishlist_synced?: number
  username?: string
  screenshots?: string[]
  source?: string
  judged?: number
}

export interface CollectionStatus {
  total: number
  last_synced: string | null
}

export interface CrawlStatus {
  total: number
  missing: number
  oldest_checked: string | null
  running?: boolean
}

export interface ScreenshotEntry {
  path: string
  url: string
}

export interface ScreenshotSession {
  session_id: string
  entries: ScreenshotEntry[]
}

export type AuthState = 'setup_required' | 'unauthenticated' | 'authenticated'

export interface SetupResponse {
  secret: string
  provisioning_uri: string
}

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
