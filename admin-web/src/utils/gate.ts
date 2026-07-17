export type GateRole = 'admin' | 'test'

const GATE_ROLE_KEY = 'sales-agent-gate-role'

export const getGateRole = (): GateRole | '' => {
  const role = sessionStorage.getItem(GATE_ROLE_KEY)
  return role === 'admin' || role === 'test' ? role : ''
}

export const setGateRole = (role: GateRole) => sessionStorage.setItem(GATE_ROLE_KEY, role)
export const clearGateRole = () => sessionStorage.removeItem(GATE_ROLE_KEY)
export const isTestGate = () => getGateRole() === 'test'
