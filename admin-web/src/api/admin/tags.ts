import request from '@/config/axios'

export interface TagPrompt {
  block_id: string
  title: string
  content: string
  shared_count: number
}

export interface ManagedTag {
  id: number
  value: string
  prompts: TagPrompt[]
}

export interface TagCategory {
  id: string
  name: string
  prompt_rule: string
  ai_assignable: boolean
  exclusive: boolean
  tags: ManagedTag[]
}

export interface TagCatalog {
  items: TagCategory[]
  total_categories: number
  total_tags: number
  total_prompts: number
}

export interface TagPromptPayload {
  block_id?: string | null
  title: string
  content: string
}

export interface CategoryPayload {
  id?: string
  name: string
  prompt_rule: string
  ai_assignable: boolean
  exclusive: boolean
}

export const getTagCatalog = () => request.get<TagCatalog>({ url: '/api/v1/admin/tags' })

export const createTagCategory = (data: CategoryPayload & { id: string }) =>
  request.post<TagCategory>({ url: '/api/v1/admin/tags/categories', data })

export const updateTagCategory = (categoryId: string, data: CategoryPayload) =>
  request.put<TagCategory>({ url: `/api/v1/admin/tags/categories/${categoryId}`, data })

export const deleteTagCategory = (categoryId: string) =>
  request.delete({ url: `/api/v1/admin/tags/categories/${categoryId}` })

export const createTag = (categoryId: string, data: { value: string; prompts: TagPromptPayload[] }) =>
  request.post<ManagedTag>({ url: `/api/v1/admin/tags/categories/${categoryId}/items`, data })

export const updateTag = (tagId: number, data: { value: string; prompts: TagPromptPayload[] }) =>
  request.put<ManagedTag>({ url: `/api/v1/admin/tags/items/${tagId}`, data })

export const deleteTag = (tagId: number) =>
  request.delete({ url: `/api/v1/admin/tags/items/${tagId}` })
