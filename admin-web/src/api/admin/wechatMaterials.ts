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

export interface BulkSendJob {
  id: number
  source_type: string
  source_id?: string | null
  w_id: string
  status: string
  total_count: number
  queued_count: number
  sent_count: number
  failed_count: number
  created_by?: string | null
  created_at: string
}

export const getWechatMaterials = (page = 1, page_size = 50, keyword = '', status = '') =>
  request.get<{ items: WechatMaterial[]; total: number }>({
    url: '/api/v1/admin/wechat-materials',
    params: { page, page_size, keyword, status: status || undefined }
  })

export const updateWechatMaterial = (id: number, data: Pick<WechatMaterial, 'name' | 'status'>) =>
  request.put<WechatMaterial>({ url: `/api/v1/admin/wechat-materials/${id}`, data })

export const createWechatMaterialFromMessage = (conversation_message_id: number, name?: string) =>
  request.post<WechatMaterial>({
    url: '/api/v1/admin/wechat-materials/from-message',
    data: { conversation_message_id, name }
  })

export const getWechatBulkJobs = (page = 1, page_size = 50) =>
  request.get<{ items: BulkSendJob[]; total: number }>({
    url: '/api/v1/admin/wechat-materials/bulk-jobs', params: { page, page_size }
  })
