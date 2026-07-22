import request from '@/config/axios'

export type AnnotationStatus = 'pending' | 'confirmed' | 'corrected' | 'uncertain' | 'excluded'

export interface IntentAnnotation {
  id: number
  trace_id: string
  status: Exclude<AnnotationStatus, 'pending'>
  primary_domain?: string | null
  secondary_domains: string[]
  primary_goal?: string | null
  secondary_goals: string[]
  issues: string[]
  scope?: string | null
  note?: string | null
  annotator_id: string
  taxonomy_version: string
  created_at: string
}

export interface IntentObservation {
  id: number
  trace_id: string
  channel: string
  user_id: string
  session_id?: string | null
  message_id?: string | null
  conversation_id?: string | null
  conversation_message_ids: number[]
  user_message: string
  taxonomy_version: string
  classifier_source: string
  classifier_provider?: string | null
  classifier_model?: string | null
  primary_domain?: string | null
  secondary_domains: string[]
  primary_goal?: string | null
  secondary_goals: string[]
  issues: string[]
  scope: string
  evidence: Record<string, unknown>[]
  confidence?: number | null
  intent_reason?: string | null
  predicted_route?: string | null
  final_route?: string | null
  primary_intent?: string | null
  sales_stage?: string | null
  annotation_status: AnnotationStatus
  annotation_origin: 'automatic' | 'human'
  needs_review: boolean
  latest_annotation?: IntentAnnotation | null
  created_at: string
  updated_at: string
}

export interface IntentObservationDetail extends IntentObservation {
  context: { role: string; content: string }[]
  raw_prediction: Record<string, unknown>
  candidate_labels: Record<string, unknown>[]
  annotation_history: IntentAnnotation[]
}

export interface IntentObservationList {
  items: IntentObservation[]
  total: number
  page: number
  page_size: number
  pending_count: number
  reviewed_count: number
  accepted_count: number
  corrected_count: number
}

export interface TaxonomyCard {
  kind: 'domain' | 'goal' | 'issue'
  id: string
  name: string
  definition: string
}

export interface IntentTaxonomy {
  version: string
  counts: Record<string, number>
  labels: TaxonomyCard[]
}

export const getIntentObservations = (params: {
  page: number
  page_size: number
  annotation_status?: string
  primary_domain?: string
  primary_goal?: string
  classifier_source?: string
  max_confidence?: number
  keyword?: string
}) => request.get<IntentObservationList>({ url: '/api/v1/admin/intent-observations', params })

export const getIntentObservation = (traceId: string) =>
  request.get<IntentObservationDetail>({
    url: `/api/v1/admin/intent-observations/${encodeURIComponent(traceId)}`
  })

export const getIntentTaxonomy = () =>
  request.get<IntentTaxonomy>({ url: '/api/v1/admin/intent-taxonomy' })

export const annotateIntentObservation = (
  traceId: string,
  data: {
    status: Exclude<AnnotationStatus, 'pending'>
    primary_domain?: string
    secondary_domains?: string[]
    primary_goal?: string
    secondary_goals?: string[]
    issues?: string[]
    scope?: string
    note?: string
    annotator_id: string
  }
) => request.post<IntentAnnotation>({
  url: `/api/v1/admin/intent-observations/${encodeURIComponent(traceId)}/annotations`,
  data
})

export const downloadIntentTrainingData = () =>
  request.get<Blob>({
    url: '/api/v1/admin/intent-training-data/export',
    params: { redact_pii: true },
    responseType: 'blob'
  })
