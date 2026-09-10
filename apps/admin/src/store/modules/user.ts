import { computed } from 'vue'
import { defineStore } from 'pinia'
import { clearGateRole, currentAccount } from '@/utils/gate'

export const useUserStore = defineStore('sales-agent-user', () => {
  const user = computed(() => ({
    id: currentAccount.value?.id || 0,
    nickname: currentAccount.value?.display_name || '',
    username: currentAccount.value?.username || ''
  }))
  return { user, reset: clearGateRole }
})
