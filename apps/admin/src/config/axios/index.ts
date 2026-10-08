import axios, { type AxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import { clearGateRole } from '@/utils/gate'
import { apiUrl, libraryApp, libraryLogin } from '@/utils/library'

const client = axios.create({
  baseURL: import.meta.env.VITE_BASE_URL || '',
  timeout: Number(import.meta.env.VITE_REQUEST_TIMEOUT || 180000)
})

client.interceptors.request.use((config) => {
  if (libraryApp) {
    config.url = apiUrl(config.url || '')
    const cookie = document.cookie.split(';').map(value => value.trim()).find(value => value.startsWith('csrftoken='))
    if (cookie) config.headers.set('X-CSRFToken', decodeURIComponent(cookie.slice('csrftoken='.length)))
  }
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
      clearGateRole()
      if (libraryApp) { window.location.replace(libraryLogin()); return Promise.reject(error) }
      if (window.location.pathname !== '/demo-chat' && window.location.pathname !== '/gate') window.location.replace(`/gate?redirect=${encodeURIComponent(window.location.pathname + window.location.search + window.location.hash)}`)
    }
    ElMessage.error({
      message: error.response?.data?.message || error.message || '网络请求失败',
      grouping: true
    })
    return Promise.reject(error)
  }
)

type RequestOptions = AxiosRequestConfig & { url: string }

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
