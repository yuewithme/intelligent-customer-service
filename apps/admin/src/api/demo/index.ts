import request from '@/config/axios'

export interface DemoChatRequest {
  customer_id: string
  customer_name?: string
  conversation_id?: string
  message: string
}

export interface DemoOutboundMessage {
  type: string
  content: string
  material_id?: number | null
  media?: Record<string, unknown> | null
  card?: Record<string, unknown> | null
}

export interface DemoHistoryMessage extends DemoOutboundMessage {
  id: number
  role: 'customer' | 'agent'
}

export interface DemoSessionResponse {
  customer_id: string
  customer_name: string
  conversation_id: string
  messages: DemoHistoryMessage[]
}

export interface DemoChatResponse {
  reply: string
  answer_segments?: string[]
  outbound_messages?: DemoOutboundMessage[]
  opening_image_url?: string | null
  customer_id: string
  conversation_id: string
  sales_stage?: string | null
  customer_tags: string[]
  product_interests: string[]
  next_action?: string | null
  need_human: boolean
  route?: string | null
  trace_id?: string | null
}

export type DemoOpeningRequest = Omit<DemoChatRequest, 'message'>

export const getActiveDemoConversation = () =>
  request.get<DemoSessionResponse>({ url: '/api/v1/demo/session' })

export const openDemoSalesConversation = (data: DemoOpeningRequest) =>
  request.post<DemoChatResponse>({ url: '/api/v1/demo/opening', data })

export const chatWithDemoSalesAgent = (data: DemoChatRequest) =>
  request.post<DemoChatResponse>({ url: '/api/v1/demo/chat', data })
