import request from '@/config/axios'

export interface UserProfile {
  user_id: string
  tenant_id: string
  channel: string
  current_stage: string
  risk_level: string
  is_human_handoff: boolean
  human_ticket_id?: string | null
  human_handoff_status?: string | null
  human_handoff_reason?: string | null
  customer_tags: string[]
  product_interests: string[]
  ai_summary?: string | null
  preference_summary?: string | null
  pain_points: string[]
  last_intent?: string | null
  last_route?: string | null
  last_template_id?: string | null
  last_active_at?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface ProfileEvent {
  id: number
  user_id: string
  tenant_id: string
  event_type: string
  before: Record<string, unknown>
  after: Record<string, unknown>
  reason?: string | null
  trace_id?: string | null
  created_at?: string | null
}

export interface ConversationMemory {
  id: number
  user_id: string
  tenant_id: string
  session_id?: string | null
  role: string
  content: string
  intent?: string | null
  route?: string | null
  template_id?: string | null
  trace_id?: string | null
  created_at?: string | null
}

export interface UserProfileBundle {
  profile: UserProfile
  recent_memories: ConversationMemory[]
  events: ProfileEvent[]
}

const userPath = (userId: string) => encodeURIComponent(userId)

export const getUserProfileBundle = (userId: string) =>
  request.get<UserProfileBundle>({
    url: `/api/v1/users/${userPath(userId)}/profile`
  })
