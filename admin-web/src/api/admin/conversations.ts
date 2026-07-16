import request from '@/config/axios'

export type ConversationStatus =
  'ai_active' | 'ai_waiting' | 'handoff_pending' | 'human_active' | 'resolved'

export interface ConversationItem {
  conversation_id: string
  channel: string
  user_id: string
  user_display_name?: string | null
  user_avatar_url?: string | null
  session_id?: string | null
  tenant_id: string
  status: ConversationStatus
  owner_id?: string | null
  last_message?: string | null
  last_route?: string | null
  last_intent?: string | null
  handoff_reason?: string | null
  handoff_ticket_id?: string | null
  unread_count: number
  created_at: string
  updated_at: string
}

export interface ConversationMessage {
  id: number
  conversation_id: string
  trace_id?: string | null
  message_id?: string | null
  delivery_status?: 'queued' | 'sent' | 'failed' | null
  sender_type: 'customer' | 'ai' | 'human' | 'system'
  sender_id?: string | null
  content: string
  route?: string | null
  primary_intent?: string | null
  metadata: Record<string, unknown>
  created_at: string
}

export interface ConversationListResponse {
  items: ConversationItem[]
  total: number
  page: number
  page_size: number
}

export interface ConversationDetail {
  conversation: ConversationItem
  messages: ConversationMessage[]
}

const conversationPath = (conversationId: string) => encodeURIComponent(conversationId)
const conversationBase = () =>
  window.location.pathname.startsWith('/demo-admin')
    ? '/api/v1/demo-admin/conversations'
    : '/api/v1/admin/conversations'

export const getConversations = (params: {
  page: number
  page_size: number
  status?: string
  owner_id?: string
  keyword?: string
}) => request.get<ConversationListResponse>({ url: conversationBase(), params })

export const getConversationDetail = (conversationId: string) =>
  request.get<ConversationDetail>({
    url: `${conversationBase()}/${conversationPath(conversationId)}`
  })

export const claimConversation = (conversationId: string, operator_id: string) =>
  request.post<ConversationItem>({
    url: `${conversationBase()}/${conversationPath(conversationId)}/claim`,
    data: { operator_id }
  })

export const replyConversation = (conversationId: string, operator_id: string, content: string) =>
  request.post<ConversationItem>({
    url: `${conversationBase()}/${conversationPath(conversationId)}/reply`,
    data: { operator_id, content }
  })

export const markConversationRead = (conversationId: string) =>
  request.post<ConversationItem>({
    url: `${conversationBase()}/${conversationPath(conversationId)}/read`
  })

export const resolveConversationMessageMedia = (messageId: number) =>
  request.post<ConversationMessage>({
    url: `/api/v1/admin/conversations/messages/${messageId}/resolve-media`
  })

export const forceHandoff = (conversationId: string, operator_id: string, reason: string) =>
  request.post<ConversationItem>({
    url: `${conversationBase()}/${conversationPath(conversationId)}/force-handoff`,
    data: { operator_id, reason }
  })

export const releaseToAi = (conversationId: string, operator_id: string) =>
  request.post<ConversationItem>({
    url: `${conversationBase()}/${conversationPath(conversationId)}/release-to-ai`,
    data: { operator_id }
  })

export const resolveConversation = (conversationId: string, operator_id: string, reason?: string) =>
  request.post<ConversationItem>({
    url: `${conversationBase()}/${conversationPath(conversationId)}/resolve`,
    data: { operator_id, reason }
  })
