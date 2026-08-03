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

export interface CaseShadowDecision {
  sales_stage: string
  route: string
  sales_action: string
  reply: string
  need_human: boolean
  next_action?: string | null
  reason: string
}

export interface CaseShadowTurnResult {
  checkpoint_id: string
  customer_message: string
  reference_reply: string
  reference_is_gold: false
  status: 'success' | 'failed'
  shadow?: CaseShadowDecision
  auto_issues?: string[]
  repair_attempted?: boolean
  error_class?: string
}

export interface ConversationCaseRun {
  run_id: string
  case_id: string
  experiment_id: string
  candidate_version: string
  prompt_version: string
  status:
    | 'pending'
    | 'running'
    | 'completed'
    | 'completed_with_errors'
    | 'failed'
  total_checkpoints: number
  completed_checkpoints: number
  failed_checkpoints: number
  error_class?: string | null
  created_at: string
  updated_at: string
  completed_at?: string | null
  result?: {
    case_id: string
    mode: string
    summary?: {
      successful_checkpoints: number
      failed_checkpoints: number
      clean_checkpoints: number
      repair_attempts: number
      issue_counts: Record<string, number>
    }
    turn_results: CaseShadowTurnResult[]
  }
}

export const getConversationCases = (params?: {
  keyword?: string
  library_type?: ConversationCaseLibrary
}) =>
  request.get<ConversationCaseList>({
    url: '/api/v1/admin/conversation-cases',
    params
  })

export const getConversationCase = (
  caseId: string,
  libraryType: ConversationCaseLibrary
) =>
  request.get<ConversationCaseDetail>({
    url: `/api/v1/admin/conversation-cases/${encodeURIComponent(caseId)}`,
    params: { library_type: libraryType }
  })

export const startConversationCaseRun = (caseId: string) =>
  request.post<ConversationCaseRun>({
    url: `/api/v1/admin/conversation-cases/${encodeURIComponent(caseId)}/runs`
  })

export const getConversationCaseRuns = (caseId: string) =>
  request.get<ConversationCaseRun[]>({
    url: '/api/v1/admin/conversation-cases/runs',
    params: { case_id: caseId, limit: 20 }
  })

export const getConversationCaseRun = (runId: string) =>
  request.get<ConversationCaseRun>({
    url: `/api/v1/admin/conversation-cases/runs/${encodeURIComponent(runId)}`
  })

export const downloadConversationCaseLibrary = (
  libraryType: ConversationCaseLibrary
) =>
  request.get<Blob>({
    url: '/api/v1/admin/conversation-cases/export',
    params: { library_type: libraryType },
    responseType: 'blob'
  })
