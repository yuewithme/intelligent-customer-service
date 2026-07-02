import request from '@/config/axios'

export interface FilePresignedUrlRespVO {
  configId: number
  uploadUrl: string
  url: string
  path: string
}

export interface FileCreateReqVO {
  configId: number
  url: string
  path: string
  name: string
  type: string
  size: number
}

export const getFilePresignedUrl = (name: string, directory?: string) =>
  request.get<FilePresignedUrlRespVO>({
    url: '/infra/file/presigned-url',
    params: { name, directory }
  })

export const createFile = (data: FileCreateReqVO) =>
  request.post({ url: '/infra/file/create', data })

export const updateFile = (
  data: { file: File; directory?: string },
  onUploadProgress?: (event: any) => void
) => {
  const formData = new FormData()
  formData.append('file', data.file)
  if (data.directory) {
    formData.append('directory', data.directory)
  }
  return request.upload({
    url: '/infra/file/upload',
    data: formData,
    onUploadProgress
  })
}
