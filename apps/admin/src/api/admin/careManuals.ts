import request from '@/config/axios'

export interface CareManualProductLink {
  youzan_item_id: string
  product_name: string
}

export interface CareManualItem {
  id: number
  source_type: 'manual' | 'course'
  price_cent: number | null
  content_count: number | null
  auto_send_allowed: boolean
  youzan_note_id: string
  note_alias?: string | null
  title: string
  orchid_name?: string | null
  aliases: string[]
  note_url: string
  cover_url?: string | null
  card_description?: string | null
  youzan_status: string
  enabled: boolean
  available: boolean
  sort_order: number
  match_keywords: string[]
  published_at?: string | null
  last_synced_at: string
  product_links: CareManualProductLink[]
}

export interface CareManualSyncRun {
  id: number
  trigger: string
  status: 'running' | 'success' | 'failed'
  scanned_count: number
  qualified_count: number
  created_count: number
  updated_count: number
  disabled_count: number
  error_message?: string | null
  started_at: string
  finished_at?: string | null
}

export interface CareManualListResponse {
  items: CareManualItem[]
  total: number
  page: number
  page_size: number
  stats: { active: number; disabled: number; unbound: number; product_unbound: number }
  last_sync?: CareManualSyncRun | null
}

export interface CareManualSyncResult {
  scanned_count: number
  qualified_count: number
  created_count: number
  updated_count: number
  disabled_count: number
  trigger: string
  status: 'success'
}

export interface CareManualPayload {
  orchid_name: string
  aliases: string[]
  youzan_item_ids: string[]
  card_description: string
  sort_order: number
  enabled: boolean
  match_keywords: string[]
}

export interface CareManualProductSuggestion {
  card_id: number
  title: string
  youzan_item_id: string
  product_name: string
  matched_name: string
  ambiguous: boolean
}

export const getCareManualProductSuggestions = () =>
  request.get<{ items: CareManualProductSuggestion[] }>({
    url: '/api/v1/admin/care-manuals/product-links/suggestions'
  })

export const addCareManualProductLinks = (links: Array<{ card_id: number; youzan_item_id: string }>) =>
  request.post<{ created_count: number; manual_count: number }>({
    url: '/api/v1/admin/care-manuals/product-links/batch',
    data: { links }
  })

export interface CareManualMatchResult {
  decision: 'unique' | 'ambiguous' | 'not_found'
  auto_send_eligible: boolean
  matches: Array<{
    card_id: number
    title: string
    orchid_name?: string | null
    note_url: string
    cover_url?: string | null
    sort_order: number
    match_type: string
    priority: number
    selected: boolean
    product_links: CareManualProductLink[]
  }>
}

export const getCareManuals = (params: {
  page: number
  page_size: number
  keyword?: string
  enabled?: boolean
  match_status?: string
  source_type?: 'manual' | 'course'
}) => request.get<CareManualListResponse>({ url: '/api/v1/admin/care-manuals', params })

export const updateCareManual = (id: number, data: CareManualPayload) =>
  request.put<CareManualItem>({ url: `/api/v1/admin/care-manuals/${id}`, data })

export const syncCareManuals = (source_type: 'manual' | 'course' = 'manual') =>
  request.post<CareManualSyncResult>({ url: '/api/v1/admin/care-manuals/sync/run', params: { source_type } })

export const importCareCourses = (urls: string[]) =>
  request.post<CareManualSyncResult>({ url: '/api/v1/admin/care-manuals/courses/import', data: { urls } })

export const testCareManualMatch = (data: {
  query: string
  product_name: string
  youzan_item_id: string
  limit: number
}) => request.post<CareManualMatchResult>({
  url: '/api/v1/admin/care-manuals/match/test',
  data
})
