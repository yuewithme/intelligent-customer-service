import request from '@/config/axios'

export interface SalesStageDefinition {
  stage: string
  display_name: string
  sequence: number
  objective: string
  entry_evidence_any: string[]
  exit_evidence_any: string[]
  allowed_actions: string[]
  allowed_knowledge_sources: string[]
  conditional_knowledge_sources: string[]
  required_slot_groups: string[][]
  prohibited_behaviors: string[]
}

export interface SalesOpportunity {
  user_id: string
  status?: string | null
  current_stage?: string | null
  previous_stage?: string | null
  stage_display_name: string
  stage_objective?: string | null
  stage_reason?: string | null
  stage_evidence: Array<Record<string, unknown> | string>
  known_slots: Record<string, unknown>
  missing_slots: string[]
  asked_slots: string[]
  decision_blocker?: Record<string, unknown> | string | null
  recommended_product_ids: string[]
  next_action?: string | null
  reply_goal?: string | null
  interruption?: { type: string; reason?: string; resume_stage?: string } | null
}

export const getSalesStages = () =>
  request.get<{ items: SalesStageDefinition[] }>({ url: '/api/admin/sales-flow/stages' })

export const getSalesOpportunity = (userId: string) =>
  request.get<SalesOpportunity>({
    url: `/api/admin/sales-flow/opportunities/${encodeURIComponent(userId)}`
  })

export const adjustSalesStage = (
  userId: string,
  data: { stage: string; reason: string; operator_id: string }
) => request.patch<SalesOpportunity>({
  url: `/api/admin/sales-flow/opportunities/${encodeURIComponent(userId)}/stage`,
  data
})
export const closeSalesOpportunity = (
  userId: string,
  data: { status: 'lost' | 'expired'; reason: string; operator_id: string }
) => request.post<SalesOpportunity>({
  url: `/api/admin/sales-flow/opportunities/${encodeURIComponent(userId)}/close`,
  data
})
