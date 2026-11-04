import request from '@/config/axios'
import { isTestGate } from '@/utils/gate'

export type ConversationStatus =
  'ai_active' | 'ai_waiting' | 'handoff_pending' | 'human_active' | 'resolved'

export interface ConversationItem {
  conversation_id: string
  channel: string
  user_id: string
  user_display_name?: string | null
  customer_remark?: string | null
  user_avatar_url?: string | null
  session_id?: string | null
  tenant_id: string
  owner_wc_id?: string | null
  owner_display_name?: string | null
  status: ConversationStatus
  owner_id?: string | null
  last_message?: string | null
  last_route?: string | null
  last_intent?: string | null
  handoff_reason?: string | null
  handoff_ticket_id?: string | null
  unread_count: number
  is_pinned: boolean
  is_muted: boolean
  marked_unread: boolean
  created_at: string
  updated_at: string
  search_score?: number
  search_matches?: {
    field: string
    label: string
    text: string
    message_id?: number
    created_at?: string
  }[]
}

export interface ConversationMessage {
  id: number
  conversation_id: string
  trace_id?: string | null
  message_id?: string | null
  delivery_status?:
    | 'queued'
    | 'sending'
    | 'waiting_material'
    | 'accepted'
    | 'unconfirmed'
    | 'delivery_unknown'
    | 'confirmed'
    | 'sent'
    | 'failed'
    | 'cancelled'
    | 'revoked'
    | null
  sender_type: 'customer' | 'ai' | 'human' | 'system'
  sender_id?: string | null
  content: string
  route?: string | null
  primary_intent?: string | null
  metadata: Record<string, unknown>
  actions?: { quote: boolean; forward: boolean; revoke: boolean; forward_same_account?: boolean }
  is_favorite?: boolean
  is_hidden?: boolean
  created_at: string
}

export interface ConversationListResponse {
  items: ConversationItem[]
  total: number
  page: number
  page_size: number
}

export interface ConversationTenant {
  tenant_id: string
  wc_id: string
  display_name?: string | null
  conversation_count: number
  unread_count: number
}

export interface ConversationTenantList {
  items: ConversationTenant[]
}

export interface AgentRelationshipState {
  customer_signal: 'none' | 'soft_refusal' | 'explicit_refusal'
  explicit_refusal: boolean
  commercial_judgment?: string
  relationship_purpose?: string
  updated_at?: string
}

export interface ConversationDetail {
  conversation: ConversationItem
  messages: ConversationMessage[]
  has_more_messages?: boolean
  agent_relationship: AgentRelationshipState
}

export interface ChatReview {
  knowledge_status?: 'found' | 'not_found' | 'unavailable'
  knowledge_count?: number
  status: 'ready' | 'empty' | 'unavailable'
  analysis: string
  suggestions: string[]
  round_count: number
  reviewed_message_ids: number[]
  reviewed_reply: string
  generated_at?: string
}

export interface YouzanOrderItem {
  title: string
  quantity: number
  image_url?: string
}

export interface YouzanOrder {
  order_no: string
  status: string
  status_text: string
  item_summary: string
  items: YouzanOrderItem[]
  payment_amount?: string | null
  express_company?: string | null
  tracking_no_masked?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface ConversationOrders {
  status: 'found' | 'not_found' | 'unbound'
  items: YouzanOrder[]
  mobile_masked?: string
  last_synced_at?: string | null
}

export interface ConversationEmoji {
  message_id: number
  url: string
  md5: string
  size: string
}

const conversationPath = (conversationId: string) => encodeURIComponent(conversationId)
const conversationBase = () =>
  isTestGate()
    ? '/api/v1/demo-admin/conversations'
    : '/api/v1/admin/conversations'

export const getConversations = (params: {
  page: number
  page_size: number
  status?: string
  owner_id?: string
  keyword?: string
  channel?: string
  test_only?: boolean
  tenant_id?: string
  for_forwarding?: boolean
  owner_wc_id?: string
}) => request.get<ConversationListResponse>({ url: conversationBase(), params })

export const getConversationTenants = () =>
  request.get<ConversationTenantList>({
    url: '/api/v1/admin/conversations/tenants'
  })

export const getConversationDetail = (conversationId: string, messageLimit?: number) =>
  request.get<ConversationDetail>({
    url: `${conversationBase()}/${conversationPath(conversationId)}`,
    params: { message_limit: messageLimit }
  })

export const updateConversationRemark = (conversationId: string, remark: string) =>
  request.put<ConversationItem>({
    url: `/api/v1/admin/conversations/${conversationPath(conversationId)}/remark`,
    data: { remark }
  })

export const claimConversation = (conversationId: string, operator_id: string) =>
  request.post<ConversationItem>({
    url: `${conversationBase()}/${conversationPath(conversationId)}/claim`,
    data: { operator_id }
  })

export const getChatReview = (conversationId: string) =>
  request.post<ChatReview>({
    url: `${conversationBase()}/${conversationPath(conversationId)}/chat-review`,
    timeout: 60_000
  })

export const updateReplyDraft = (conversationId: string, data: {
  operator_id: string
  editor_id: string
  active: boolean
  remaining_seconds?: number
}) => request.post<ConversationItem>({
  url: `${conversationBase()}/${conversationPath(conversationId)}/reply-draft`,
  data,
  timeout: 10_000
})

export const replyConversation = (conversationId: string, operator_id: string, content: string, source_message_id?: number) =>
  request.post<ConversationItem>({
    url: `${conversationBase()}/${conversationPath(conversationId)}/reply`,
    data: { operator_id, content, source_message_id }
  })

export const revokeConversationMessage = (messageId: number, operator_id: string) =>
  request.post<ConversationMessage>({
    url: `/api/v1/admin/conversations/messages/${messageId}/revoke`, data: { operator_id }
  })

export const updateMessagePreference = (messageId: number, action: 'favorite' | 'unfavorite' | 'hide', operator_id: string) =>
  request.post<ConversationMessage>({
    url: `/api/v1/admin/conversations/messages/${messageId}/preference`, data: { action, operator_id }
  })

export const getMessageText = (messageId: number, action: 'extract' | 'translate') =>
  request.post<{ text: string }>({
    url: `/api/v1/admin/conversations/messages/${messageId}/text`, data: { action }
  })

export const getMessageMedia = (messageId: number, localSource?: string) =>
  request.get<Blob>({
    url: localSource || `/api/v1/admin/conversations/message-media/${messageId}`, responseType: 'blob'
  })

export const forwardConversationMessage = (conversationId: string, operator_id: string, source_message_id: number) =>
  request.post<ConversationItem>({
    url: `${conversationBase()}/${conversationPath(conversationId)}/forward`,
    data: { operator_id, source_message_id }
  })

export const getConversationFavorites = (conversationId: string, page: number) =>
  request.get<{ items: ConversationMessage[]; total: number; page: number; page_size: number }>({
    url: `${conversationBase()}/${conversationPath(conversationId)}/favorites`,
    params: { page, page_size: 20 }
  })

export interface ReplyProduct {
  item_id: string
  title: string
  image_url?: string | null
  price_cent?: number | null
  stock?: number | null
  h5_url?: string | null
  page_url?: string | null
}

export const getReplyProducts = (params: { page: number; page_size: number; keyword?: string }) =>
  request.get<{ items: ReplyProduct[]; total: number }>({ url: '/api/v1/admin/conversations/products', params })

export const replyConversationProduct = (conversationId: string, operator_id: string, item_id: string) =>
  request.post<ConversationItem>({
    url: `${conversationBase()}/${conversationPath(conversationId)}/reply-product`,
    data: { operator_id, item_id }
  })

export const markConversationRead = (conversationId: string, automatic = false) =>
  request.post<ConversationItem>({
    url: `${conversationBase()}/${conversationPath(conversationId)}/read`,
    params: { automatic }
  })

export type ConversationListAction = 'hide' | 'pin' | 'unpin' | 'unread' | 'mute' | 'unmute'

export const applyConversationListAction = (conversationId: string, action: ConversationListAction) =>
  request.post<{ items: ConversationItem[] }>({
    url: `${conversationBase()}/${conversationPath(conversationId)}/list-action`,
    data: { action }
  })

export const replyConversationImage = (
  conversationId: string,
  operatorId: string,
  file: File
) => {
  const data = new FormData()
  data.append('operator_id', operatorId)
  data.append('file', file)
  return request.upload<ConversationItem>({
    url: `${conversationBase()}/${conversationPath(conversationId)}/reply-image`,
    data
  })
}

export const replyConversationVideo = (conversationId: string, operatorId: string, file: File) => {
  const data = new FormData()
  data.append('operator_id', operatorId)
  data.append('file', file)
  return request.upload<ConversationItem>({
    url: `${conversationBase()}/${conversationPath(conversationId)}/reply-video`,
    data
  })
}

export const replyConversationCareManual = (
  conversationId: string,
  operator_id: string,
  care_manual_id: number
) =>
  request.post<ConversationItem>({
    url: `${conversationBase()}/${conversationPath(conversationId)}/reply-care-manual`,
    data: { operator_id, care_manual_id }
  })

export const getConversationEmojis = (conversationId: string) =>
  request.get<{ items: ConversationEmoji[] }>({
    url: `${conversationBase()}/${conversationPath(conversationId)}/emojis`
  })

export const replyConversationEmoji = (
  conversationId: string,
  operator_id: string,
  source_message_id: number
) =>
  request.post<ConversationItem>({
    url: `${conversationBase()}/${conversationPath(conversationId)}/reply-emoji`,
    data: { operator_id, source_message_id }
  })

export const getConversationOrders = (conversationId: string) =>
  request.get<ConversationOrders>({
    url: `${conversationBase()}/${conversationPath(conversationId)}/orders`
  })

export const hideConversation = (conversationId: string) =>
  request.post<ConversationItem>({
    url: `${conversationBase()}/${conversationPath(conversationId)}/hide`
  })

export const resolveConversationMessageMedia = (messageId: number) =>
  request.post<ConversationMessage>({
    url: `/api/v1/admin/conversations/messages/${messageId}/resolve-media`
  })

export const transcribeConversationMessage = (messageId: number) =>
  request.post<ConversationMessage>({
    url: `/api/v1/admin/conversations/messages/${messageId}/transcribe`
  })

export const retryConversationMessageDelivery = (messageId: number) =>
  request.post<ConversationMessage>({
    url: `/api/v1/admin/conversations/messages/${messageId}/retry-delivery`
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
