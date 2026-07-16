import request from '@/config/axios'

export interface DemoChatRequest {
  customer_id: string
  customer_name?: string
  conversation_id?: string
  message: string
}

export interface DemoChatResponse {
  reply: string
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

export const chatWithDemoSalesAgent = (data: DemoChatRequest) =>
  request.post<DemoChatResponse>({ url: '/api/v1/demo/chat', data })
