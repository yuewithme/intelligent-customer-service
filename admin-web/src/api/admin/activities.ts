import request from '@/config/axios'

export type ActivityStatus = 'published' | 'archived'
export type ActivityEffectiveStatus = ActivityStatus | 'active' | 'disabled' | 'scheduled' | 'expired'
export type ActivityMessageType = 'text' | 'received_image' | 'received_video'

export interface ActivityMessageItem {
  position: number
  type: ActivityMessageType
  content?: string
  preview_url?: string | null
  source_message_id: number
}

export interface ActivityItem {
  id: number
  title: string
  summary?: string | null
  status: ActivityStatus
  effective_status: ActivityEffectiveStatus
  enabled: boolean
  ai_enabled: boolean
  ai_rules: Record<string, unknown>
  items: ActivityMessageItem[]
  item_count: number
  valid_from?: string | null
  valid_until?: string | null
  created_by: string
  updated_by: string
  created_at: string
  updated_at: string
  published_at?: string | null
}

export interface ActivityListResponse {
  items: ActivityItem[]
  total: number
  page: number
  page_size: number
}

export interface ActivitySendResult {
  id: number
  activity_id: number
  status: string
  outbound_message_ids: number[]
}

export interface ActivitySendLog {
  id: number
  activity_id: number
  conversation_id: string
  user_id: string
  trigger_mode: 'manual' | 'ai'
  operator_id?: string | null
  outbound_message_ids: number[]
  status: 'queued' | 'sending' | 'retrying' | 'sent' | 'partial'
  last_error?: string | null
  created_at: string
}

export const getActivities = (params: {
  page: number
  page_size: number
  status?: string
  keyword?: string
  conversation_id?: string
}) => request.get<ActivityListResponse>({ url: '/api/v1/admin/activities', params })

export const getActivity = (activityId: number) =>
  request.get<ActivityItem>({ url: `/api/v1/admin/activities/${activityId}` })

export const createActivityFromMessages = (data: {
  conversation_id: string
  message_ids: number[]
  title: string
  summary?: string
  operator_id: string
  valid_from?: string
  valid_until?: string
}) => request.post<ActivityItem>({ url: '/api/v1/admin/activities/from-messages', data })

export const updateActivity = (
  activityId: number,
  data: {
    title: string
    summary?: string
    operator_id: string
    valid_from?: string
    valid_until?: string
    ai_rules?: Record<string, unknown>
  }
) => request.put<ActivityItem>({ url: `/api/v1/admin/activities/${activityId}`, data })

export const publishActivity = (activityId: number, operator_id: string) =>
  request.post<ActivityItem>({
    url: `/api/v1/admin/activities/${activityId}/publish`,
    data: { operator_id }
  })

export const updateActivitySwitches = (
  activityId: number,
  data: { operator_id: string; enabled?: boolean; ai_enabled?: boolean }
) => request.put<ActivityItem>({ url: `/api/v1/admin/activities/${activityId}/switches`, data })

export const archiveActivity = (activityId: number, operator_id: string) =>
  request.post<ActivityItem>({
    url: `/api/v1/admin/activities/${activityId}/archive`,
    data: { operator_id }
  })

export const sendActivity = (
  activityId: number,
  conversation_id: string,
  operator_id: string
) =>
  request.post<ActivitySendResult>({
    url: `/api/v1/admin/activities/${activityId}/send`,
    data: { conversation_id, operator_id }
  })

export const getActivitySendLogs = (activityId: number) =>
  request.get<{ items: ActivitySendLog[]; total: number }>({
    url: `/api/v1/admin/activities/${activityId}/send-logs`
  })
