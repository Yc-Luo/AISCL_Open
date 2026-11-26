import type { CanonicalStageId } from '../../lib/stageModel'
import api from './client'

export type ExportProfile = 'participation' | 'artifacts' | 'research'
export type ExportStatus = 'queued' | 'running' | 'completed' | 'failed' | 'expired'

export interface ExportGroup {
  id: string
  name: string
  is_archived: boolean
}

export interface CreateExportJob {
  profile: ExportProfile
  project_ids?: string[]
  from_date?: string
  through_date?: string
  stage_id?: CanonicalStageId
  include_files?: boolean
}

export interface ExportJob {
  id: string
  profile: ExportProfile
  status: ExportStatus
  course_id: string
  project_ids: string[]
  from_date: string | null
  through_date: string | null
  stage_id: string | null
  include_files: boolean
  progress: number
  message: string
  filename: string | null
  file_size: number
  missing_files: number
  created_at: string
}

const coursePath = (courseId: string) => `/courses/${encodeURIComponent(courseId)}`
const jobsPath = (courseId: string) => `${coursePath(courseId)}/export-jobs`
const jobPath = (courseId: string, jobId: string) => `${jobsPath(courseId)}/${encodeURIComponent(jobId)}`

function resolveDownloadUrl(downloadUrl: string): string {
  const base = new URL(`${api.getUri().replace(/\/+$/, '')}/`, window.location.origin)
  // A ticket may return a full API path or a path relative to the API prefix.
  const path = downloadUrl.startsWith('/courses/') ? downloadUrl.slice(1) : downloadUrl
  const url = new URL(path, base)
  if (url.origin !== base.origin || !url.pathname.startsWith(base.pathname) || url.username || url.password) {
    throw new Error('Unexpected export download URL')
  }
  return url.href
}

export const courseExportService = {
  async getGroups(courseId: string, signal: AbortSignal): Promise<ExportGroup[]> {
    const { data } = await api.get<{ groups: ExportGroup[] }>(`${coursePath(courseId)}/export-groups`, { signal })
    return data.groups
  },
  async getJobs(courseId: string, signal: AbortSignal): Promise<ExportJob[]> {
    const { data } = await api.get<{ jobs: ExportJob[] }>(jobsPath(courseId), { signal })
    return data.jobs
  },
  async getJob(courseId: string, jobId: string, signal: AbortSignal): Promise<ExportJob> {
    const { data } = await api.get<ExportJob>(jobPath(courseId, jobId), { signal })
    return data
  },
  async createJob(courseId: string, options: CreateExportJob, signal: AbortSignal): Promise<ExportJob> {
    const { data } = await api.post<ExportJob>(jobsPath(courseId), options, { signal })
    return data
  },
  async getDownloadTicket(courseId: string, jobId: string, signal: AbortSignal) {
    const { data } = await api.post<{ download_url: string; expires_at: string }>(
      `${jobPath(courseId, jobId)}/download-ticket`, undefined, { signal, withCredentials: true },
    )
    return { ...data, download_url: resolveDownloadUrl(data.download_url) }
  },
}
