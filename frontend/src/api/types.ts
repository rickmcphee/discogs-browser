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
  ebay_app_id?: string
  ebay_cert_id?: string
}

export type SortField = 'artist' | 'title' | 'year' | 'label' | 'format' | string
export type SortOrder = 'asc' | 'desc'

export interface CrawlEvent {
  status?: 'found' | 'not_found' | 'error' | 'complete' | 'started' | 'stopped' | 'ping'
  discogs_id?: string
  release?: string
  artist?: string
  site?: string
  price?: number
  error?: string
  total?: number
  screenshots?: string[]
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
