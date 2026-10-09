import { ref } from 'vue'
import { libraryApp, libraryPages } from './library'

export type GateRole = 'admin' | 'test' | 'employee'
export interface Account {
  id: string
  username: string
  display_name: string
  role: GateRole
  enabled: boolean
  pages: string[]
  wechat_ids: string[]
  all_wechat: boolean
}

export const currentAccount = ref<Account | null>(null)
export const getGateRole = (): GateRole | '' => currentAccount.value?.role || ''
export const setAccount = (account: Account) => { currentAccount.value = account }
export const clearGateRole = () => { currentAccount.value = null }
export const isTestGate = () => getGateRole() === 'test'
export const isAdmin = () => getGateRole() === 'admin'
export const canViewPage = (path: string) => (!libraryApp && isAdmin()) || Boolean(currentAccount.value?.pages.includes(path))
export const firstAllowedPage = () => libraryApp
  ? libraryPages[libraryApp].find(canViewPage) || '/no-access'
  : isAdmin() ? '/workbench' : currentAccount.value?.pages.find(page => page !== '/operations/conversation-cases') || '/no-access'
