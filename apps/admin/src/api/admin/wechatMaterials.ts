import request from '@/config/axios'

export interface WechatMaterial {
  id: number
  name: string
  media_type: 'image' | 'video'
  preview_url?: string | null
  source_w_id?: string | null
  source_wc_id?: string | null
  source_message_id?: string | null
  status: 'ready' | 'expired' | 'disabled'
  last_error?: string | null
  last_verified_at?: string | null
  created_at: string
  updated_at: string
}

export const createWechatMaterialFromMessage = (conversation_message_id: number, name?: string) =>
  request.post<WechatMaterial>({
    url: '/api/v1/admin/wechat-materials/from-message',
    data: { conversation_message_id, name }
  })
