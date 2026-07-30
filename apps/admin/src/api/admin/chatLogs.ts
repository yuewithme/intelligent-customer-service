import request from '@/config/axios'

export interface ChatLogItem {
  trace_id: string
  channel: string
  user_id: string
  session_id?: string | null
  user_message: string
  answer?: string | null
  route?: string | null
  reply_type?: string | null
  primary_intent?: string | null
  sales_stage?: string | null
  next_action?: string | null
  need_human: boolean
  latency_ms?: number | null
  status: string
  error_message?: string | null
  evaluation_id?: string | null
  created_at: string
}

export interface ChatLogDetail extends ChatLogItem {
  metadata: Record<string, unknown>
  policy_reason?: string | null
  intent_reason?: string | null
  sources: Record<string, unknown>[]
  usage: Record<string, unknown>
  stage_latencies: Record<string, unknown>
}

export interface ChatLogList {
  items: ChatLogItem[]
  total: number
  page: number
  page_size: number
}

export const getChatLogs = (params: {
  page: number
  page_size: number
  keyword?: string
  user_id?: string
  session_id?: string
  status?: string
}) => request.get<ChatLogList>({ url: '/api/v1/admin/chat-logs', params })

export const getChatLog = (traceId: string) =>
  request.get<ChatLogDetail>({
    url: `/api/v1/admin/chat-logs/${encodeURIComponent(traceId)}`
  })
