import { ref } from 'vue'

export type GateRole = 'admin' | 'test' | 'employee'
export interface Account {
  id: number
  username: string
  display_name: string
  role: GateRole
  enabled: boolean
  pages: string[]
  wechat_ids: string[]
}

export const currentAccount = ref<Account | null>(null)
export const getGateRole = (): GateRole | '' => currentAccount.value?.role || ''
export const setAccount = (account: Account) => { currentAccount.value = account }
export const clearGateRole = () => { currentAccount.value = null }
export const isTestGate = () => getGateRole() === 'test'
export const isAdmin = () => getGateRole() === 'admin'
export const canViewPage = (path: string) => isAdmin() || Boolean(currentAccount.value?.pages.includes(path))
export const firstAllowedPage = () => isAdmin() ? '/workbench' : currentAccount.value?.pages[0] || '/no-access'
