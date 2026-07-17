import request from '@/config/axios'

export type SopMessageType = 'text' | 'image' | 'video'

export interface SopMessageItem {
  message_type: SopMessageType
  content: string
  preview_url?: string | null
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
  send_time: string
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

export const getUnpurchasedSop = () =>
  request.get<{
    sop: UnpurchasedSopConfig
    steps: UnpurchasedSopStep[]
    stats: Record<string, number>
  }>({ url: '/api/v1/admin/unpurchased-sop' })

export const updateUnpurchasedSop = (data: Pick<UnpurchasedSopConfig, 'name' | 'enabled' | 'dry_run' | 'send_window_start' | 'send_window_end' | 'contact_poll_interval_minutes' | 'contact_missing_threshold'>) =>
  request.put<UnpurchasedSopConfig>({ url: '/api/v1/admin/unpurchased-sop', data })

export const createUnpurchasedSopStep = (data: SopStepPayload) =>
  request.post<UnpurchasedSopStep>({ url: '/api/v1/admin/unpurchased-sop/steps', data })

export const updateUnpurchasedSopStep = (stepId: number, data: SopStepPayload) =>
  request.put<UnpurchasedSopStep>({ url: `/api/v1/admin/unpurchased-sop/steps/${stepId}`, data })

export const deleteUnpurchasedSopStep = (stepId: number) =>
  request.delete<{ id: number; deleted: boolean }>({ url: `/api/v1/admin/unpurchased-sop/steps/${stepId}` })

export const syncUnpurchasedSopContacts = () =>
  request.post<Record<string, number | string | boolean>>({
    url: '/api/v1/admin/unpurchased-sop/contacts/sync',
    timeout: 240000
  })

export const getUnpurchasedSopContacts = (page = 1, page_size = 50) =>
  request.get<{ items: UnpurchasedSopContact[]; total: number }>({
    url: '/api/v1/admin/unpurchased-sop/contacts',
    params: { page, page_size }
  })

export const getUnpurchasedSopDeliveries = (page = 1, page_size = 50) =>
  request.get<{ items: UnpurchasedSopDelivery[]; total: number }>({
    url: '/api/v1/admin/unpurchased-sop/deliveries',
    params: { page, page_size }
  })

export const testSendUnpurchasedSop = (step_id: number, contact_id: number) =>
  request.post<{ outbound_message_id: number; outbound_message_ids: number[]; status: string }>({
    url: '/api/v1/admin/unpurchased-sop/test-send',
    data: { step_id, contact_id }
  })

export const uploadUnpurchasedSopMedia = (file: File) => {
  const data = new FormData()
  data.append('file', file)
  return request.upload<SopMediaUpload>({
    url: '/api/v1/admin/unpurchased-sop/media/upload',
    data,
    timeout: 240000
  })
}
