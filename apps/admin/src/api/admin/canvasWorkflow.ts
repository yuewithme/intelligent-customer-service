import request from '@/config/axios'

export type CanvasNodeType = 'start' | 'general' | 'scheduled_send' | 'condition' | 'handoff' | 'end'
export interface CanvasBranch { id: string; condition: string }
export type CanvasContentLibrary = 'care_manual' | 'cloud_video' | 'cloud_image' | 'beginner_care' | 'care_course' | 'sales_activity'
export interface CanvasContentSource { library: CanvasContentLibrary; category: string; selection: 'auto' | 'specific'; item_ids: string[] }
export interface CanvasContentItem { id: string; name: string; category: string; description: string }
export interface CanvasContentLibraryOption {
  id: CanvasContentLibrary
  name: string
  unit: string
  description: string
  available: boolean
  count: number | null
  categories: Array<{ id: string; name: string; description: string; count: number | null }>
  items: CanvasContentItem[]
  message: string
}
export interface CanvasNode {
  id: string
  type: CanvasNodeType
  name: string
  position: { x: number; y: number }
  trigger_condition: string
  action: string
  exit_condition: string
  schedule_rule: string
  send_content_type: 'text' | 'image' | 'video' | 'link' | 'product_card' | 'mixed'
  branch_mode: 'first_match' | 'all_matches'
  handoff_target: string
  branches: CanvasBranch[]
  send_time: string
  send_cycle: 'daily' | 'every_three_days' | 'weekly'
  send_weekday: number | null
  content_source: CanvasContentSource | null
}
export interface CanvasEdge { id: string; source: string; target: string; label: string; source_branch: string | null }
export interface CanvasDefinition { name: string; nodes: CanvasNode[]; edges: CanvasEdge[] }
export interface CanvasIssue {
  node_id: string | null
  field: 'name' | 'trigger_condition' | 'action' | 'exit_condition' | 'schedule_rule' | 'send_content_type' | 'branch_mode' | 'handoff_target' | 'branches' | 'send_time' | 'send_cycle' | 'content_source' | 'graph'
  message: string
  suggestion: string
}
export interface CanvasReview {
  summary: string
  nodes: Array<{ node_id: string; understanding: string }>
  issues: CanvasIssue[]
}
export interface CanvasWorkflow {
  workflow_id: string
  revision: number
  definition: CanvasDefinition
  published_revision: number | null
  published_at: string | null
  execution: { status: 'draft' | 'active' | 'inactive'; message: string; scope?: string; revision?: number; enabled?: boolean }
  last_review: CanvasReview | null
  last_review_revision: number | null
  review_mode: 'full' | 'incremental' | 'reused' | 'validation' | null
  review_base_revision: number | null
  published: { definition: CanvasDefinition; review: CanvasReview; model: string; revision: number; published_at: string } | null
}
export interface CanvasSummary { workflow_id: string; name: string }
export interface CanvasChatMessage { role: 'user' | 'assistant'; content: string }
const base = '/api/v1/admin/orchestration/workbench/canvas/workflows'
export const listCanvasContentSources = () => request.get<CanvasContentLibraryOption[]>({ url: '/api/v1/admin/orchestration/workbench/canvas/content-sources' })
export const listCanvasWorkflows = () => request.get<CanvasSummary[]>({ url: base })
export const syncExistingSops = () => request.post<{ created: string[]; updated: string[] }>({ url: '/api/v1/admin/orchestration/workbench/canvas/sync-sops' })
export const setCanvasWorkflowEnabled = (scope: string, enabled: boolean) => request.put({ url: '/api/v1/admin/orchestration/workbench/sop-enabled', data: { sop_scope: scope, enabled } })
export const createCanvasWorkflow = () => request.post<CanvasWorkflow>({ url: base, data: { name: '未命名流程' } })
export const getCanvasWorkflow = (id: string) => request.get<CanvasWorkflow>({ url: `${base}/${id}` })
export const saveCanvasWorkflow = (id: string, revision: number, definition: CanvasDefinition) =>
  request.put<CanvasWorkflow>({ url: `${base}/${id}`, data: { revision, definition } })
export const editCanvasWorkflow = (id: string, revision: number, definition: CanvasDefinition, message: string, history: CanvasChatMessage[], selectedNodeId: string | null) =>
  request.post<{ workflow_id: string; revision: number; reply: string; definition: CanvasDefinition | null }>({
    url: `${base}/${id}/chat`, data: { revision, definition, message, history, selected_node_id: selectedNodeId }, timeout: 235000
  })
export const publishCanvasWorkflow = (id: string, revision: number) =>
  request.post<{ published: boolean; review: CanvasReview; workflow: CanvasWorkflow }>({
    url: `${base}/${id}/publish`, data: { revision }, timeout: 235000
  })
export const fixCanvasWorkflowIssue = (id: string, revision: number, definition: CanvasDefinition, reviewRevision: number, issue: CanvasIssue) =>
  request.post<{ workflow_id: string; revision: number; reply: string; definition: CanvasDefinition | null }>({
    url: `${base}/${id}/fix-issue`, data: { revision, definition, review_revision: reviewRevision, issue }, timeout: 235000
  })
