import request from '@/config/axios'

export interface ProductSku {
  sku_id: string
  spec_name: string
  price_cent?: number | null
  stock?: number | null
  sku_code?: string | null
  image_url?: string | null
}

export interface ProductItem {
  item_id: string
  title: string
  image_url?: string | null
  status: 'on_sale' | 'off_shelf' | 'sold_out' | 'missing'
  price_cent?: number | null
  stock?: number | null
  h5_url?: string | null
  page_url?: string | null
  sort_order: number
  internal_note?: string | null
  youzan_updated_at?: string | null
  last_synced_at: string
  sku_count: number
  skus: ProductSku[]
}

export interface ProductOption {
  item_id: string
  title: string
  status: ProductItem['status']
  image_url?: string | null
  linked: boolean
}

export interface ProductKnowledgePayload {
  item_id?: string | null
  product_name: string
  category: string
  flower_color: string
  fragrance: string
  flowering_status: string
  price_budget: string
  care_scenes: string
  bloom_period: string
  audience_tag: string
  market_price: string
  highlighted_features: string
  sales_copy: string
}

export interface ProductKnowledgeItem extends ProductKnowledgePayload {
  id: number
  linked_product?: Pick<ProductOption, 'item_id' | 'title' | 'status' | 'image_url'> | null
  created_at: string
  updated_at: string
}

export interface ProductKnowledgeListResponse {
  items: ProductKnowledgeItem[]
  total: number
  page: number
  page_size: number
  linked_count: number
}

export interface ProductSyncRun {
  id: number
  trigger: 'manual' | 'scheduled'
  status: 'running' | 'success' | 'failed'
  product_count: number
  sku_count: number
  detail_error_count: number
  error_message?: string | null
  started_at: string
  finished_at?: string | null
}

export interface ProductListResponse {
  items: ProductItem[]
  total: number
  page: number
  page_size: number
  last_sync?: ProductSyncRun | null
}

export const getProducts = (params: {
  page: number
  page_size: number
  keyword?: string
  status?: string
  sort_by: string
  sort_direction: string
}) => request.get<ProductListResponse>({ url: '/api/v1/admin/products', params })

export const syncProducts = () =>
  request.post<{ product_count: number; sku_count: number; detail_error_count: number }>({
    url: '/api/v1/admin/products/sync'
  })

export const getProductOptions = () =>
  request.get<ProductOption[]>({ url: '/api/v1/admin/products/options' })

export const getProductKnowledge = (params: {
  page: number
  page_size: number
  keyword?: string
  linked?: boolean
}) => request.get<ProductKnowledgeListResponse>({
  url: '/api/v1/admin/products/knowledge',
  params
})

export const createProductKnowledge = (data: ProductKnowledgePayload) =>
  request.post<ProductKnowledgeItem>({
    url: '/api/v1/admin/products/knowledge',
    data
  })

export const updateProductKnowledge = (id: number, data: ProductKnowledgePayload) =>
  request.put<ProductKnowledgeItem>({
    url: `/api/v1/admin/products/knowledge/${id}`,
    data
  })

export const deleteProductKnowledge = (id: number) =>
  request.delete<{ deleted: boolean }>({
    url: `/api/v1/admin/products/knowledge/${id}`
  })

export const updateProductSort = (itemId: string, sortOrder: number) =>
  request.put<ProductItem>({
    url: `/api/v1/admin/products/${encodeURIComponent(itemId)}/sort`,
    data: { sort_order: sortOrder }
  })

export const updateProductNote = (itemId: string, internalNote: string) =>
  request.put<ProductItem>({
    url: `/api/v1/admin/products/${encodeURIComponent(itemId)}/note`,
    data: { internal_note: internalNote }
  })
