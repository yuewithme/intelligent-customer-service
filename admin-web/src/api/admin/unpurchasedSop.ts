import request from '@/config/axios'

export type SopMessageType = 'text' | 'image' | 'video' | 'material'
export type SopKind = 'unpurchased' | 'service'

const sopBaseUrl = (kind: SopKind) => `/api/v1/admin/${kind === 'service' ? 'service-sop' : 'unpurchased-sop'}`

export interface SopMessageItem {
  message_type: SopMessageType
  content: string
  preview_url?: string | null
  material_id?: number | null
}

export interface UnpurchasedSopConfig {
  id: number
  name: string
  enabled: boolean
  dry_run: boolean
  send_window_start: string
  send_window_end: string
  contact_poll_interval_minutes: number
  contact_missing_threshold: number
  timezone: string
  baseline_initialized_at?: string | null
  last_contact_sync_at?: string | null
  updated_at: string
}

export interface UnpurchasedSopStep {
  id: number
  sop_id: number
  day_offset: number
  send_time: string
  send_time_start: string
  send_time_end: string
  message_type: SopMessageType
  content: string
  preview_url?: string | null
  messages: SopMessageItem[]
  position: number
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface UnpurchasedSopContact {
  id: number
  wc_id: string
  display_name?: string | null
  remark_name?: string | null
  wechat_id?: string | null
  avatar_url?: string | null
  friend_added_on?: string | null
  first_seen_at: string
  last_seen_at: string
  status: string
  discovery_source: string
  customer_tags: string[]
  enrollment_status?: string | null
  exit_reason?: string | null
}

export interface UnpurchasedSopDelivery {
  id: number
  contact_id?: number | null
  wc_id?: string | null
  display_name?: string | null
  step_id: number
  due_at: string
  status: string
  message_type: SopMessageType
  content: string
  preview_url?: string | null
  outbound_message_id?: number | null
  outbound_message_ids: number[]
  messages: SopMessageItem[]
  attempts: number
  last_error?: string | null
  sent_at?: string | null
  created_at: string
}

export interface SopStepPayload {
  day_offset: number
  send_time_start: string
  send_time_end: string
  messages: SopMessageItem[]
  position: number
  enabled: boolean
}

export interface SopMediaUpload {
  type: 'image' | 'video'
  name: string
  size: number
  url: string
  relative_url: string
}

export const getUnpurchasedSop = (kind: SopKind = 'unpurchased') =>
  request.get<{
    sop: UnpurchasedSopConfig
    steps: UnpurchasedSopStep[]
    stats: Record<string, number>
  }>({ url: sopBaseUrl(kind) })

export const updateUnpurchasedSop = (data: Pick<UnpurchasedSopConfig, 'name' | 'enabled' | 'dry_run' | 'send_window_start' | 'send_window_end' | 'contact_poll_interval_minutes' | 'contact_missing_threshold'>, kind: SopKind = 'unpurchased') =>
  request.put<UnpurchasedSopConfig>({ url: sopBaseUrl(kind), data })

export const createUnpurchasedSopStep = (data: SopStepPayload, kind: SopKind = 'unpurchased') =>
  request.post<UnpurchasedSopStep>({ url: `${sopBaseUrl(kind)}/steps`, data })

export const updateUnpurchasedSopStep = (stepId: number, data: SopStepPayload, kind: SopKind = 'unpurchased') =>
  request.put<UnpurchasedSopStep>({ url: `${sopBaseUrl(kind)}/steps/${stepId}`, data })

export const deleteUnpurchasedSopStep = (stepId: number, kind: SopKind = 'unpurchased') =>
  request.delete<{ id: number; deleted: boolean }>({ url: `${sopBaseUrl(kind)}/steps/${stepId}` })

export const syncUnpurchasedSopContacts = (kind: SopKind = 'unpurchased') =>
  request.post<Record<string, number | string | boolean>>({
    url: `${sopBaseUrl(kind)}/contacts/sync`,
    timeout: 240000
  })

export const getUnpurchasedSopContacts = (page = 1, page_size = 50, keyword = '', kind: SopKind = 'unpurchased') =>
  request.get<{ items: UnpurchasedSopContact[]; total: number }>({
    url: `${sopBaseUrl(kind)}/contacts`,
    params: { page, page_size, keyword }
  })

export const getUnpurchasedSopDeliveries = (page = 1, page_size = 50, kind: SopKind = 'unpurchased') =>
  request.get<{ items: UnpurchasedSopDelivery[]; total: number }>({
    url: `${sopBaseUrl(kind)}/deliveries`,
    params: { page, page_size }
  })

export const testSendUnpurchasedSop = (step_id: number, contact_ids: number[], kind: SopKind = 'unpurchased') =>
  request.post<{ contact_count: number; results: Array<{ contact_id: number; outbound_message_ids: number[]; status: string }>; outbound_message_id: number; outbound_message_ids: number[]; status: string }>({
    url: `${sopBaseUrl(kind)}/test-send`,
    data: { step_id, contact_ids }
  })

export const uploadUnpurchasedSopMedia = (file: File, kind: SopKind = 'unpurchased') => {
  const data = new FormData()
  data.append('file', file)
  return request.upload<SopMediaUpload>({
    url: `${sopBaseUrl(kind)}/media/upload`,
    data,
    timeout: 240000
  })
}
