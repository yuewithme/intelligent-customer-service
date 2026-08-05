import request from '@/config/axios'

export type ConversationCaseLibrary = 'complete' | 'cleaned'

export interface ConversationCaseSummary {
  case_id: string
  legacy_case_id?: string | null
  library_type: ConversationCaseLibrary
  usage: string
  source_file: string
  content_quality: string
  preview: string
  turn_count: number
  message_count: number
  customer_turn_count: number
  reference_turn_count: number
  checkpoint_count: number
}

export interface ConversationCaseTurn {
  turn_id: string
  role: 'customer' | 'merchant'
  messages: string[]
  reference_only: boolean
}

export interface ConversationCaseDetail extends ConversationCaseSummary {
  schema_version: string
  reference_policy: string
  turns: ConversationCaseTurn[]
  checkpoints: Array<{
    checkpoint_id: string
    turn_id: string
    customer_message: string
    reference_reply: string
  }>
}

export interface ConversationCaseList {
  items: ConversationCaseSummary[]
  total: number
  library_counts: Record<ConversationCaseLibrary, number>
}

export const getConversationCases = (params?: {
  keyword?: string
  library_type?: ConversationCaseLibrary
}) => request.get<ConversationCaseList>({ url: '/api/v1/admin/conversation-cases', params })

export const getConversationCase = (
  caseId: string,
  libraryType: ConversationCaseLibrary
) => request.get<ConversationCaseDetail>({
  url: `/api/v1/admin/conversation-cases/${encodeURIComponent(caseId)}`,
  params: { library_type: libraryType }
})

export const downloadConversationCaseLibrary = (
  libraryType: ConversationCaseLibrary
) => request.get<Blob>({
  url: '/api/v1/admin/conversation-cases/export',
  params: { library_type: libraryType },
  responseType: 'blob'
})
