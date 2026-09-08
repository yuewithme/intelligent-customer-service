import request from '@/config/axios'

export type CapabilityVisibility = 'hidden' | 'configurable' | 'draggable'
export type CapabilityKind = 'query' | 'action' | 'human' | 'internal'
export type CapabilityAiMode = 'automatic' | 'conditional' | 'workflow_only' | 'human_confirm'
export type CapabilityCustomerContact = 'none' | 'reply_support' | 'direct_message' | 'direct_card' | 'conversation_handoff'
export type CapabilityRiskLevel = 'low' | 'medium' | 'high'

export interface ConditionClause {
  kind: 'fact' | 'semantic'
  description: string
  path?: string | null
  operator?: string | null
  value?: unknown
}

export interface ConditionGroup {
  mode: 'all' | 'any'
  conditions: ConditionClause[]
}

export interface CapabilityUsage {
  package_id: string
  package_name: string
  step_id: string
  step_name: string
  usage: 'allowed' | 'required' | 'action'
}

export interface CapabilityItem {
  schema_version: string
  capability_id: string
  version: string
  name: string
  description: string
  kind: CapabilityKind
  status: 'draft' | 'published' | 'deprecated'
  ui: {
    visibility: CapabilityVisibility
    group: string
    icon?: string | null
    summary: string
  }
  business: {
    action: string
    data_source: string
    ai_mode: CapabilityAiMode
    ai_mode_description: string
    customer_contact: CapabilityCustomerContact
    customer_contact_description: string
    staff_notification: boolean
    permission_description: string
    result_description: string
    risk_level: CapabilityRiskLevel
    risk_description: string
  }
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
  config_schema: Record<string, unknown>
  execution: {
    adapter: string
    handler_key: string
    timeout_seconds: number
    idempotency: string
    retry: { max_attempts: number; backoff_seconds: number }
  }
  permissions: string[]
  side_effects: string[]
  preconditions?: ConditionGroup | null
  usage_guidance: string[]
  error_codes: string[]
  used_by: CapabilityUsage[]
}

export interface ExperienceCollectedFact {
  key: string
  description: string
  required_for_completion: boolean
}

export interface ExperienceStepCapability {
  capability_id: string
  usage: 'allowed' | 'required'
  purpose: string
}

export interface ExperienceStep {
  schedule?: { time: string; copy_type: '名品故事' | '养护科普' | '话题种草'; match_preferences: boolean } | null
  require_product_interest?: boolean
  step_id: string
  node_id?: string
  name: string
  type: 'agent_stage' | 'decision' | 'action' | 'wait'
  description?: string | null
  goal?: string
  directions?: string[]
  collect?: ExperienceCollectedFact[]
  capabilities?: ExperienceStepCapability[]
  completion?: ConditionGroup
  strategy?: 'rules' | 'agent' | 'hybrid'
  capability_id?: string
  arguments?: Record<string, unknown>
  on_error_outcome?: string | null
  resume_events?: string[]
  timeout?: {
    value?: number | null
    parameter?: string | null
    unit: 'seconds' | 'minutes' | 'hours' | 'days'
  }
  handoff_enabled?: boolean
}

export interface ExperienceTransition {
  transition_id: string
  from_step: string
  to_step?: string | null
  outcome?: string | null
  label: string
  priority: number
  condition?: ConditionGroup | null
}

export interface ExperienceOutcome {
  outcome_id: string
  name: string
  terminal: boolean
  next_package_id?: string | null
  result_tags: string[]
}

export interface ExperiencePackage {
  flow_revision: number
  entry_rule: { required_tags: string[]; tag_categories: string[]; excluded_tags: string[]; fallback_only: boolean }
  sop_scope: 'first_order' | 'service' | 'seeding'
  enabled: boolean
  schema_version: string
  package_id: string
  version: string
  name: string
  description: string
  status: 'draft' | 'published' | 'deprecated'
  tags: string[]
  goals: string[]
  entry: {
    events: string[]
    start_step_id: string
    conditions?: ConditionGroup | null
    priority: number
  }
  parameters_schema: Record<string, unknown>
  context_schema: Record<string, unknown>
  global_rules: string[]
  capability_dependencies: Array<{
    capability_id: string
    version_constraint: string
    required: boolean
  }>
  steps: ExperienceStep[]
  transitions: ExperienceTransition[]
  outcomes: ExperienceOutcome[]
  success_metrics: string[]
  layout: Record<string, { x: number; y: number }>
}

export interface CapabilityWorkbenchResponse {
  tag_categories: Array<{ id: string; name: string; values: string[] }>
  read_only: boolean
  source: string
  stats: {
    capabilities_total: number
    hidden_total: number
    configurable_total: number
    draggable_total: number
    experience_packages_total: number
    draft_packages_total: number
  }
  capabilities: CapabilityItem[]
  experience_packages: ExperiencePackage[]
}

export const getCapabilityWorkbench = () =>
  request.get<CapabilityWorkbenchResponse>({
    url: '/api/v1/admin/orchestration/workbench'
  })

export const updateWorkbenchSopNodeHandoff = (data: {
  node_id: string
  handoff_enabled: boolean
}) =>
  request.put<{
    sop_scope: 'first_order' | 'service' | 'seeding'
    node_id: string
    name: string
    description: string
    handoff_enabled: boolean
    updated_at: string
  }>({
    url: '/api/v1/admin/orchestration/workbench/sop-node-handoff',
    data
  })

export const updateWorkbenchSopEnabled = (data: {
  sop_scope: ExperiencePackage['sop_scope']
  enabled: boolean
}) => request.put<{ sop_scope: ExperiencePackage['sop_scope']; enabled: boolean }>({
  url: '/api/v1/admin/orchestration/workbench/sop-enabled',
  data
})

export const saveWorkbenchFlow = (item: ExperiencePackage) => request.put<{ revision: number }>({
  url: `/api/v1/admin/orchestration/workbench/flows/${item.sop_scope}`,
  data: {
    revision: item.flow_revision,
    start_step_id: item.entry.start_step_id,
    entry_rule: item.entry_rule,
    layout: item.layout,
    steps: item.steps.map(step => ({
      step_id: step.step_id, name: step.name, type: step.type,
      description: step.description || '', goal: step.goal || '', directions: step.directions || [],
      handoff_enabled: Boolean(step.handoff_enabled), require_product_interest: Boolean(step.require_product_interest),
      schedule: step.schedule || null
    })),
    transitions: item.transitions.map(edge => ({
      transition_id: edge.transition_id, from_step: edge.from_step, to_step: edge.to_step || null,
      outcome: edge.to_step ? null : 'complete', label: edge.label, priority: edge.priority
    }))
  }
})
