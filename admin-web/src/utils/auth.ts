const TOKEN_KEY = 'sales-agent-access-token'

export const getAccessToken = () => localStorage.getItem(TOKEN_KEY) || ''
export const setAccessToken = (token: string) => localStorage.setItem(TOKEN_KEY, token)
export const clearAccessToken = () => localStorage.removeItem(TOKEN_KEY)
export const setDefaultAccessToken = () => setAccessToken(import.meta.env.VITE_DEFAULT_API_KEY || 'change_me')
