import request from '@/config/axios'

export interface HandoffNotificationContact {
  id: number
  wc_id: string
  display_name?: string | null
  remark_name?: string | null
  wechat_id?: string | null
  avatar_url?: string | null
  status: string
}

export interface HandoffNotificationSettings {
  global_handoff_enabled: boolean
  recipient_contact_ids: number[]
  recipients: HandoffNotificationContact[]
  message_text: string
  sop_node_handoff: Record<string, boolean>
  sop_node_groups: HandoffSopNodeGroup[]
  updated_at: string
}

export interface HandoffSopNodeGroup {
  sop_scope: 'first_order' | 'service'
  name: string
  nodes: Array<{
    node_id: string
    name: string
    description: string
  }>
}

export const getHandoffNotificationSettings = () =>
  request.get<HandoffNotificationSettings>({
    url: '/api/v1/admin/handoff-notification'
  })

export const updateHandoffNotificationSettings = (data: {
  global_handoff_enabled: boolean
  recipient_contact_ids: number[]
  message_text: string
  sop_node_handoff: Record<string, boolean>
}) =>
  request.put<HandoffNotificationSettings>({
    url: '/api/v1/admin/handoff-notification',
    data
  })

export const getHandoffNotificationContacts = (keyword = '') =>
  request.get<{
    items: HandoffNotificationContact[]
    total: number
    page: number
    page_size: number
  }>({
    url: '/api/v1/admin/handoff-notification/contacts',
    params: { page: 1, page_size: 100, keyword }
  })

export const syncHandoffNotificationContacts = () =>
  request.post<Record<string, number | string | boolean>>({
    url: '/api/v1/admin/handoff-notification/contacts/sync',
    timeout: 240000
  })
