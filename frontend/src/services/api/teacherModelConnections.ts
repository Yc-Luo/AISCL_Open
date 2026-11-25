import api from './client'

export type TeacherModelProvider = 'openai' | 'deepseek' | 'siliconflow' | 'custom'

export interface TeacherModelConnection {
  id: string
  name: string
  provider: TeacherModelProvider
  model: string
  base_url: string
  has_api_key: boolean
}

export interface TeacherModelConnectionInput {
  name: string
  provider: TeacherModelProvider
  model: string
  api_key?: string
  base_url?: string
}

const path = '/teacher-model-connections'
const connectionPath = (id: string) => `${path}/${encodeURIComponent(id)}`

export const teacherModelConnectionsService = {
  async list() {
    const { data } = await api.get<{ connections: TeacherModelConnection[] }>(path)
    return data.connections
  },
  async create(input: TeacherModelConnectionInput & { api_key: string }) {
    await api.post(path, input)
  },
  async update(id: string, input: TeacherModelConnectionInput) {
    await api.put(connectionPath(id), input)
  },
  async remove(id: string) {
    await api.delete(connectionPath(id))
  },
  async test(id: string) {
    const { data } = await api.post<{ success: boolean; message: string }>(`${connectionPath(id)}/test`)
    return data.success
  },
}
