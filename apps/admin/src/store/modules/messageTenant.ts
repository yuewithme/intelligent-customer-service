import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import {
  getConversationTenants,
  type ConversationTenant
} from '@/api/admin/conversations'

const STORAGE_KEY = 'sales-agent.message-tenant-id'

export const useMessageTenantStore = defineStore('messageTenant', () => {
  const tenants = ref<ConversationTenant[]>([])
  const selectedTenantId = ref(localStorage.getItem(STORAGE_KEY) || '')
  const loading = ref(false)
  let pendingLoad: Promise<void> | undefined

  const selectedTenant = computed(() =>
    tenants.value.find((item) => item.tenant_id === selectedTenantId.value)
  )

  const selectTenant = (tenantId: string) => {
    selectedTenantId.value = tenantId
    if (tenantId) {
      localStorage.setItem(STORAGE_KEY, tenantId)
    } else {
      localStorage.removeItem(STORAGE_KEY)
    }
  }

  const loadTenants = async (options: { silent?: boolean } = {}) => {
    if (pendingLoad) {
      await pendingLoad
      return
    }
    if (!options.silent) loading.value = true
    pendingLoad = (async () => {
      const data = await getConversationTenants()
      tenants.value = data.items
      if (!tenants.value.some((item) => item.tenant_id === selectedTenantId.value)) {
        selectTenant(tenants.value[0]?.tenant_id || '')
      }
    })()
    try {
      await pendingLoad
    } finally {
      pendingLoad = undefined
      if (!options.silent) loading.value = false
    }
  }

  return {
    tenants,
    selectedTenantId,
    selectedTenant,
    loading,
    selectTenant,
    loadTenants
  }
})
