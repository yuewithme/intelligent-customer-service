import axios, { type AxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import { clearAccessToken, getAccessToken } from '@/utils/auth'
import { clearGateRole } from '@/utils/gate'

const client = axios.create({
  baseURL: import.meta.env.VITE_BASE_URL || '',
  timeout: Number(import.meta.env.VITE_REQUEST_TIMEOUT || 180000)
})

client.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

client.interceptors.response.use(
  (response) => {
    const body = response.data
    if (response.config.responseType === 'blob' || response.config.responseType === 'arraybuffer') return body
    if (body?.code === 0 || body?.code === 200 || body?.code === undefined) return body
    return Promise.reject(new Error(body.message || '请求失败'))
  },
  (error) => {
    if (error.response?.status === 401) {
      clearAccessToken()
      clearGateRole()
      if (window.location.pathname !== '/demo-chat') window.location.assign('/gate')
    }
    ElMessage.error(error.response?.data?.message || error.message || '网络请求失败')
    return Promise.reject(error)
  }
)

type RequestOptions = AxiosRequestConfig & { url: string; headersType?: string }

const unwrap = async <T>(promise: Promise<any>): Promise<T> => {
  const result = await promise
  return (result?.data ?? result) as T
}

export default {
  get: <T = unknown>(options: RequestOptions) => unwrap<T>(client.request({ ...options, method: 'GET' })),
  post: <T = unknown>(options: RequestOptions) => unwrap<T>(client.request({ ...options, method: 'POST' })),
  patch: <T = unknown>(options: RequestOptions) => unwrap<T>(client.request({ ...options, method: 'PATCH' })),
  put: <T = unknown>(options: RequestOptions) => unwrap<T>(client.request({ ...options, method: 'PUT' })),
  delete: <T = unknown>(options: RequestOptions) => unwrap<T>(client.request({ ...options, method: 'DELETE' })),
  upload: <T = unknown>(options: RequestOptions) => unwrap<T>(client.request({ ...options, method: 'POST', headers: { ...options.headers, 'Content-Type': 'multipart/form-data' } }))
}
