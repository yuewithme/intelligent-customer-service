import request from '@/config/axios'

export type CanvasNodeType = 'start' | 'general' | 'end'
export interface CanvasNode {
  id: string
  type: CanvasNodeType
  name: string
  position: { x: number; y: number }
  trigger_condition: string
  action: string
  exit_condition: string
}
export interface CanvasEdge { id: string; source: string; target: string; label: string }
export interface CanvasDefinition { name: string; nodes: CanvasNode[]; edges: CanvasEdge[] }
export interface CanvasIssue {
  node_id: string | null
  field: 'name' | 'trigger_condition' | 'action' | 'exit_condition' | 'graph'
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
  last_review: CanvasReview | null
  published: { definition: CanvasDefinition; review: CanvasReview; model: string; revision: number; published_at: string } | null
}
export interface CanvasSummary { workflow_id: string; name: string }
const base = '/api/v1/admin/orchestration/workbench/canvas/workflows'
export const listCanvasWorkflows = () => request.get<CanvasSummary[]>({ url: base })
export const syncExistingSops = () => request.post<{ created: string[] }>({ url: '/api/v1/admin/orchestration/workbench/canvas/sync-sops' })
export const createCanvasWorkflow = () => request.post<CanvasWorkflow>({ url: base, data: { name: '未命名流程' } })
export const getCanvasWorkflow = (id: string) => request.get<CanvasWorkflow>({ url: `${base}/${id}` })
export const saveCanvasWorkflow = (id: string, revision: number, definition: CanvasDefinition) =>
  request.put<CanvasWorkflow>({ url: `${base}/${id}`, data: { revision, definition } })
export const publishCanvasWorkflow = (id: string, revision: number) =>
  request.post<{ published: boolean; review: CanvasReview; workflow: CanvasWorkflow }>({
    url: `${base}/${id}/publish`, data: { revision }, timeout: 230000
  })
