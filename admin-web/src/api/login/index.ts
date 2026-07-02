import request from '@/config/axios'
import type { TokenType } from './types'

export interface AdminLoginVO {
  token?: string
  username?: string
  password?: string
}

const toToken = (value: string): TokenType => ({
  id: 1,
  accessToken: value,
  refreshToken: '',
  userId: 1,
  userType: 1,
  clientId: 'admin-web',
  expiresTime: Date.now() + 1000 * 60 * 60 * 24 * 30
})

export const login = async (data: AdminLoginVO): Promise<TokenType> => {
  const token = (data.token || data.password || data.username || '').trim()
  if (!token) {
    throw new Error('请输入 API Key')
  }
  return toToken(token)
}

export const loginOut = async () => {
  return { code: 0, message: 'success', data: null }
}

export const getInfo = async () => {
  return {
    permissions: ['*:*:*'],
    roles: ['admin'],
    user: {
      id: 1,
      avatar: '',
      nickname: '管理员',
      deptId: 0
    },
    menus: []
  }
}

export const checkAdminToken = () => {
  return request.get({ url: '/health' })
}
