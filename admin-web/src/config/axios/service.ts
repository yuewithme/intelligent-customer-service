import axios, { AxiosError, AxiosInstance, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { ElMessage, ElNotification } from 'element-plus'
import qs from 'qs'
import { config } from '@/config/axios/config'
import { getAccessToken, removeToken } from '@/utils/auth'

const { base_url, request_timeout } = config

const service: AxiosInstance = axios.create({
  baseURL: base_url,
  timeout: request_timeout,
  withCredentials: false,
  paramsSerializer: (params) => qs.stringify(params, { allowDots: true })
})

service.interceptors.request.use(
  (requestConfig: InternalAxiosRequestConfig) => {
    const token = getAccessToken()
    if (token) {
      requestConfig.headers.Authorization = `Bearer ${token}`
    }
    if (requestConfig.method?.toUpperCase() === 'GET') {
      requestConfig.headers['Cache-Control'] = 'no-cache'
      requestConfig.headers.Pragma = 'no-cache'
    }
    return requestConfig
  },
  (error: AxiosError) => Promise.reject(error)
)

service.interceptors.response.use(
  (response: AxiosResponse<any>) => {
    const data = response.data
    if (
      response.request.responseType === 'blob' ||
      response.request.responseType === 'arraybuffer'
    ) {
      return response.data
    }
    const code = data?.code ?? 0
    if (code === 0 || code === 200) {
      return data
    }
    if (response.status === 401 || code === 401 || code === 40100) {
      removeToken()
      ElMessage.error('登录已失效，请重新登录')
      window.location.href = '/login'
      return Promise.reject(new Error(data?.message || 'Unauthorized'))
    }
    const message = data?.message || data?.msg || '请求失败'
    ElNotification.error({ title: message })
    return Promise.reject(new Error(message))
  },
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      removeToken()
      ElMessage.error('登录已失效，请重新登录')
      window.location.href = '/login'
      return Promise.reject(error)
    }
    const message =
      error.message === 'Network Error'
        ? '网络连接失败'
        : error.message.includes('timeout')
          ? '请求超时'
          : error.message
    ElMessage.error(message)
    return Promise.reject(error)
  }
)

export { service }
