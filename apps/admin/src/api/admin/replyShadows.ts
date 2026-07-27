import request from '@/config/axios'

export type ReplyShadowVerdict =
  | 'primary_better'
  | 'shadow_better'
  | 'tie'
  | 'both_bad'
  | 'uncertain'
  | 'excluded'

export interface ShadowFollowUp {
  needed: boolean
  action?: string | null
  due_in_hours?: number | null
  cancel_conditions: string[]
}

export interface ShadowDecision {
  sales_stage?: string | null
  route?: string | null
  sales_action?: string | null
  reply?: string
  need_human?: boolean
  next_action?: string | null
  follow_up?: ShadowFollowUp
  facts_used?: string[]
  confidence?: number | null
  reason?: string | null
}

export interface ReplyShadowAnnotation {
  id: number
  trace_id: string
  verdict: ReplyShadowVerdict
  error_tags: string[]
  note?: string | null
  annotator_id: string
  created_at: string
}

export interface ReplyShadowRun {
  id: number
  trace_id: string
  experiment_id: string
  candidate_version: string
  prompt_version: string
  channel: string
  user_message: string
  primary: ShadowDecision
  shadow: ShadowDecision
  decision_agreement: boolean
  auto_issues: string[]
  review_priority: 'high' | 'medium' | 'low'
  status: 'success' | 'failed'
  error_class?: string | null
  latency_ms: number
  latest_annotation?: ReplyShadowAnnotation | null
  created_at: string
  updated_at: string
}

export interface ReplyShadowDetail extends ReplyShadowRun {
  input_snapshot: Record<string, unknown>
  annotation_history: ReplyShadowAnnotation[]
}

export interface ReplyShadowList {
  items: ReplyShadowRun[]
  total: number
  page: number
  page_size: number
  pending_count: number
  reviewed_count: number
}

export const getReplyShadows = (params: {
  page: number
  page_size: number
  status?: string
  review_status?: string
  review_priority?: string
  keyword?: string
}) => request.get<ReplyShadowList>({ url: '/api/v1/admin/reply-shadows', params })

export const getReplyShadow = (traceId: string) =>
  request.get<ReplyShadowDetail>({
    url: `/api/v1/admin/reply-shadows/${encodeURIComponent(traceId)}`
  })

export const annotateReplyShadow = (
  traceId: string,
  data: {
    verdict: ReplyShadowVerdict
    error_tags?: string[]
    note?: string
    annotator_id: string
  }
) => request.post<ReplyShadowAnnotation>({
  url: `/api/v1/admin/reply-shadows/${encodeURIComponent(traceId)}/annotations`,
  data
})

export const downloadReplyShadowDataset = () =>
  request.get<Blob>({
    url: '/api/v1/admin/reply-shadows/export',
    params: { redact_pii: true },
    responseType: 'blob'
  })
