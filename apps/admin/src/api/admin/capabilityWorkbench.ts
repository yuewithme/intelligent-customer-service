import request from '@/config/axios'

export type CapabilityVisibility = 'hidden' | 'configurable' | 'draggable'
export type CapabilityKind = 'query' | 'action' | 'human' | 'internal'

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
  step_id: string
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
