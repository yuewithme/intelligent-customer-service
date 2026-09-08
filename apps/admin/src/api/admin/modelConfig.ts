import request from '@/config/axios'

export interface EyunAccountSettings {
  w_id: string
  wc_id: string
}

export const getEyunAccountSettings = () =>
  request.get<EyunAccountSettings>({ url: '/api/v1/admin/eyun-settings' })

export const updateEyunAccountSettings = (data: EyunAccountSettings) =>
  request.put<EyunAccountSettings>({ url: '/api/v1/admin/eyun-settings', data })
