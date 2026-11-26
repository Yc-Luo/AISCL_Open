import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { isAxiosError } from 'axios'
import { ChevronDown, Download, Plus, RotateCcw } from 'lucide-react'
import { CANONICAL_STAGES, STAGE_LABELS, normalizeStageId, type CanonicalStageId } from '../../lib/stageModel'
import {
  courseExportService, type CreateExportJob, type ExportGroup, type ExportJob, type ExportProfile, type ExportStatus,
} from '../../services/api/courseExports'

const profiles: Record<ExportProfile, string> = { participation: '参与情况', artifacts: '小组成果', research: '研究数据' }
const statuses: Record<ExportStatus, string> = { queued: '排队中', running: '生成中', completed: '已完成', failed: '生成失败', expired: '已过期' }
const isPending = (job: ExportJob) => job.status === 'queued' || job.status === 'running'
const field = 'w-full min-w-0 rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm'
const action = 'inline-flex items-center gap-1.5 rounded-md border border-slate-300 px-2.5 py-1.5 text-sm font-medium hover:bg-slate-50 disabled:opacity-50'
const pollInterval = 3000

function httpStatus(error: unknown) {
  return isAxiosError(error) ? error.response?.status : undefined
}

function accessDenied(error: unknown) {
  const status = httpStatus(error)
  return status === 401 || status === 403
}

function originalOptions(job: ExportJob): CreateExportJob {
  const stage = normalizeStageId(job.stage_id)
  return {
    profile: job.profile,
    ...(job.project_ids.length ? { project_ids: job.project_ids } : {}),
    ...(job.from_date ? { from_date: job.from_date } : {}),
    ...(job.through_date ? { through_date: job.through_date } : {}),
    ...(job.profile === 'research' && stage ? { stage_id: stage } : {}),
    ...(job.profile === 'artifacts' ? { include_files: job.include_files } : {}),
  }
}

export default function CourseExports({ courseId, embedded = false }: { courseId: string; embedded?: boolean }) {
  const panelId = useId()
  const [openedCourse, setOpenedCourse] = useState<string | null>(null)
  const expanded = openedCourse === courseId

  if (embedded) {
    return <section aria-label="班级数据导出"><ExportPanel key={courseId} courseId={courseId} /></section>
  }

  return (
    <section className="border-b border-slate-200 pb-3">
      <h2>
        <button type="button" aria-expanded={expanded} aria-controls={panelId}
          className="flex w-full items-center justify-between gap-2 py-2 text-left font-semibold hover:text-indigo-700"
          onClick={() => setOpenedCourse(expanded ? null : courseId)}>
          成果与导出 <ChevronDown size={18} aria-hidden="true" className={`shrink-0 ${expanded ? 'rotate-180' : ''}`} />
        </button>
      </h2>
      <div id={panelId} hidden={!expanded}>
        {expanded && <ExportPanel key={courseId} courseId={courseId} />}
      </div>
    </section>
  )
}

function ExportPanel({ courseId }: { courseId: string }) {
  const [load, setLoad] = useState<'loading' | 'ready' | 'error' | 'denied'>('loading')
  const [attempt, setAttempt] = useState(0)
  const [groups, setGroups] = useState<ExportGroup[]>([])
  const [jobs, setJobs] = useState<ExportJob[]>([])
  const [profile, setProfile] = useState<ExportProfile>('participation')
  const [limitGroups, setLimitGroups] = useState(false)
  const [projectIds, setProjectIds] = useState<string[]>([])
  const [fromDate, setFromDate] = useState('')
  const [throughDate, setThroughDate] = useState('')
  const [stage, setStage] = useState<CanonicalStageId | ''>('')
  const [includeFiles, setIncludeFiles] = useState(false)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState('')
  const [pollError, setPollError] = useState(false)
  const session = useRef<AbortController | null>(null)
  const invalidDates = Boolean(fromDate && throughDate && fromDate > throughDate)
  const pendingIds = JSON.stringify(jobs.filter(isPending).map(job => job.id).sort())

  const denyAccess = useCallback(() => {
    session.current?.abort()
    setLoad('denied')
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller
    session.current = controller
    Promise.all([courseExportService.getGroups(courseId, signal), courseExportService.getJobs(courseId, signal)]).then(
      ([groups, jobs]) => {
        if (signal.aborted) return
        setGroups(groups)
        setJobs(jobs)
        setLoad('ready')
      },
      error => {
        if (signal.aborted) return
        if (accessDenied(error)) denyAccess()
        else setLoad('error')
      },
    )
    return () => controller.abort()
  }, [attempt, courseId, denyAccess])

  useEffect(() => {
    const ids: string[] = JSON.parse(pendingIds)
    if (load !== 'ready' || !ids.length) return
    const controller = new AbortController()
    const { signal } = controller
    let timer = setTimeout(poll, pollInterval)

    // Wait for the current round to finish before polling again.
    async function poll() {
      const results = await Promise.allSettled(ids.map(id => courseExportService.getJob(courseId, id, signal)))
      if (signal.aborted || session.current?.signal.aborted) return
      if (results.some(result => result.status === 'rejected' && accessDenied(result.reason))) {
        denyAccess()
        return
      }
      const updates = new Map<string, ExportJob>()
      results.forEach(result => {
        if (result.status === 'fulfilled') updates.set(result.value.id, result.value)
      })
      setJobs(current => current.map(job => updates.get(job.id) ?? job))
      setPollError(updates.size !== ids.length)
      timer = setTimeout(poll, pollInterval)
    }

    return () => { clearTimeout(timer); controller.abort() }
  }, [courseId, denyAccess, load, pendingIds])

  async function runAction(action: (signal: AbortSignal) => Promise<void>, errorMessage: (error: unknown) => string) {
    const signal = session.current?.signal
    if (!signal || signal.aborted || busy) return
    setBusy(true)
    setActionError('')
    try {
      await action(signal)
    } catch (error) {
      if (signal.aborted) return
      if (accessDenied(error)) denyAccess()
      else setActionError(errorMessage(error))
    } finally {
      if (!signal.aborted) setBusy(false)
    }
  }

  function create(options: CreateExportJob) {
    void runAction(async signal => {
      const job = await courseExportService.createJob(courseId, options, signal)
      if (!signal.aborted) setJobs(current => [job, ...current.filter(previous => previous.id !== job.id)])
    }, error => httpStatus(error) === 409
      ? '已有导出任务正在进行，请等待完成后再生成。'
      : '未能确认创建导出任务，请重新打开此栏核对任务记录后再试。')
  }

  function download(job: ExportJob) {
    void runAction(async signal => {
      const ticket = await courseExportService.getDownloadTicket(courseId, job.id, signal).catch(async error => {
        if (httpStatus(error) !== 409 || signal.aborted) throw error
        const updated = await courseExportService.getJob(courseId, job.id, signal)
        if (!signal.aborted) setJobs(current => current.map(previous => previous.id === job.id ? updated : previous))
        if (updated.status === 'completed') throw error
        return null
      })
      if (!ticket || signal.aborted) return
      const link = document.createElement('a')
      link.href = ticket.download_url
      link.download = job.filename ?? ''
      document.body.append(link)
      link.click()
      link.remove()
    }, () => '无法下载文件。若文件已过期，请重新生成。')
  }

  if (load === 'loading') return <p role="status" className="py-2 text-sm text-slate-600">正在加载导出记录…</p>
  if (load === 'denied') return <p role="alert" className="py-2 text-sm text-red-700">登录已失效或没有本班导出权限。</p>
  if (load === 'error') return (
    <div className="flex flex-wrap items-center gap-2 py-2 text-sm">
      <p role="alert">无法加载班级导出。</p>
      <button type="button" className={action} onClick={() => { setLoad('loading'); setAttempt(attempt + 1) }}>
        <RotateCcw size={14} aria-hidden="true" /> 重试加载
      </button>
    </div>
  )

  const groupNames = new Map(groups.map(group => [group.id, group.name]))
  const recent = [...jobs].sort((first, second) => Date.parse(second.created_at) - Date.parse(first.created_at))

  return (
    <div className="min-w-0 space-y-4 py-2">
      <form className="space-y-3" onSubmit={event => {
        event.preventDefault()
        if (invalidDates || (limitGroups && !projectIds.length)) return
        create({
          profile,
          ...(limitGroups ? { project_ids: projectIds } : {}),
          ...(fromDate ? { from_date: fromDate } : {}),
          ...(throughDate ? { through_date: throughDate } : {}),
          ...(profile === 'research' && stage ? { stage_id: stage } : {}),
          ...(profile === 'artifacts' ? { include_files: includeFiles } : {}),
        })
      }}>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <label className="grid min-w-0 gap-1 text-xs font-medium">导出类型
            <select className={field} value={profile} onChange={event => setProfile(event.target.value as ExportProfile)}>
              {Object.entries(profiles).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select>
          </label>
          <label className="grid min-w-0 gap-1 text-xs font-medium">小组范围
            <select className={field} value={limitGroups ? 'selected' : 'all'} onChange={event => setLimitGroups(event.target.value === 'selected')}>
              <option value="all">全部小组（含已归档）</option>
              <option value="selected" disabled={!groups.length}>指定小组</option>
            </select>
          </label>
          <label className="grid min-w-0 gap-1 text-xs font-medium">开始日期
            <input className={field} type="date" value={fromDate} onChange={event => setFromDate(event.target.value)} />
          </label>
          <label className="grid min-w-0 gap-1 text-xs font-medium">截止日期（含当日）
            <input className={field} type="date" value={throughDate} aria-invalid={invalidDates}
              onChange={event => setThroughDate(event.target.value)} />
          </label>
        </div>
        {invalidDates && <p role="alert" className="text-sm text-red-700">截止日期不能早于开始日期。</p>}
        {limitGroups && (
          <fieldset className="min-w-0">
            <legend className="mb-1 text-xs font-medium">指定小组</legend>
            <div className="grid max-h-40 gap-x-4 gap-y-1 overflow-auto rounded-md border border-slate-200 p-2 sm:grid-cols-2">
              {groups.map(group => (
                <label key={group.id} className="flex min-w-0 items-start gap-2 text-sm">
                  <input className="mt-1 shrink-0" type="checkbox" checked={projectIds.includes(group.id)} onChange={event => {
                    const checked = event.target.checked
                    setProjectIds(current => checked ? [...current, group.id] : current.filter(id => id !== group.id))
                  }} />
                  <span className="break-words">{group.name}{group.is_archived ? '（已归档）' : ''}</span>
                </label>
              ))}
            </div>
          </fieldset>
        )}
        <div className="flex flex-wrap items-end gap-3">
          {profile === 'research' && (
            <label className="grid min-w-0 gap-1 text-xs font-medium">学习阶段
              <select className={field} value={stage} onChange={event => setStage(normalizeStageId(event.target.value) ?? '')}>
                <option value="">全部阶段</option>
                {CANONICAL_STAGES.map(id => <option key={id} value={id}>{STAGE_LABELS[id]}</option>)}
              </select>
            </label>
          )}
          {profile === 'artifacts' && (
            <label className="flex items-center gap-2 py-1.5 text-sm">
              <input type="checkbox" checked={includeFiles} onChange={event => setIncludeFiles(event.target.checked)} /> 包含附件
            </label>
          )}
          <button type="submit" disabled={busy || invalidDates || (limitGroups && !projectIds.length)}
            className="inline-flex items-center gap-1.5 rounded-md bg-indigo-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-800 disabled:opacity-50">
            <Plus size={16} aria-hidden="true" /> 创建导出
          </button>
        </div>
      </form>
      {actionError && <p role="alert" className="text-sm text-red-700">{actionError}</p>}
      <section className="border-t border-slate-200 pt-3">
        <h3 className="text-sm font-semibold">最近导出</h3>
        {pollError && <p role="status" className="py-1 text-sm text-slate-600">暂时无法更新部分任务状态，正在重试。</p>}
        {!recent.length && <p className="py-2 text-sm text-slate-600">暂无导出任务。</p>}
        {recent.length > 0 && (
          <ul className="max-h-96 divide-y divide-slate-200 overflow-auto">
            {recent.map(job => {
              const stage = normalizeStageId(job.stage_id)
              const scopes = [
                job.project_ids.length ? job.project_ids.map(id => groupNames.get(id) ?? id).join('、') : '全部小组',
                job.from_date || job.through_date ? `${job.from_date ?? '不限'} 至 ${job.through_date ?? '不限'}` : '',
                stage ? STAGE_LABELS[stage] : '',
                job.profile === 'artifacts' && job.include_files ? '含附件' : '',
              ].filter(Boolean)
              return (
                <li key={job.id} className="flex flex-wrap items-start justify-between gap-2 py-2.5 text-sm">
                  <div className="min-w-0 flex-1 basis-52 space-y-1 break-words">
                    <p className="font-medium">{profiles[job.profile]} <span className={job.status === 'failed' ? 'text-red-700' : 'text-slate-600'}>· {statuses[job.status]}</span></p>
                    <p className="text-xs text-slate-600">{new Date(job.created_at).toLocaleString('zh-CN')} · {scopes.join(' · ')}</p>
                    {isPending(job) && <progress className="block h-1.5 w-36 max-w-full accent-indigo-700" aria-label="生成进度" max={100} value={job.progress} />}
                    {job.message && <p className="text-slate-600">{job.message}</p>}
                    {job.filename && <p className="text-slate-600">{job.filename}{job.file_size > 0 ? ` · ${fileSize(job.file_size)}` : ''}</p>}
                    {job.missing_files > 0 && <p className="text-amber-800">缺少 {job.missing_files} 个附件</p>}
                  </div>
                  {!isPending(job) && (
                    <div className="flex flex-wrap gap-2">
                      {job.status === 'completed' && <button type="button" disabled={busy} className={action} onClick={() => download(job)}>
                        <Download size={14} aria-hidden="true" /> 下载
                      </button>}
                      <button type="button" disabled={busy} className={action} onClick={() => create(originalOptions(job))}>
                        <RotateCcw size={14} aria-hidden="true" /> {job.status === 'failed' ? '重试' : '重新生成'}
                      </button>
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </section>
    </div>
  )
}

function fileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`
}
