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
