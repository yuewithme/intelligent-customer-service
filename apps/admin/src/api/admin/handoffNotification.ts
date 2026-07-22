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
  recipient_contact_ids: number[]
  recipients: HandoffNotificationContact[]
  message_text: string
  updated_at: string
}

export const getHandoffNotificationSettings = () =>
  request.get<HandoffNotificationSettings>({
    url: '/api/v1/admin/handoff-notification'
  })

export const updateHandoffNotificationSettings = (data: {
  recipient_contact_ids: number[]
  message_text: string
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
