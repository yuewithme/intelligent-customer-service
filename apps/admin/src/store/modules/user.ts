import { defineStore } from 'pinia'

const NAME_KEY = 'sales-agent-operator-name'

export const useUserStore = defineStore('sales-agent-user', {
  state: () => ({ user: { id: 1, nickname: localStorage.getItem(NAME_KEY) || '管理员' } }),
  actions: {
    setNickname(nickname: string) {
      this.user.nickname = nickname
      localStorage.setItem(NAME_KEY, nickname)
    },
    reset() {
      this.user = { id: 1, nickname: '管理员' }
      localStorage.removeItem(NAME_KEY)
    }
  }
})
