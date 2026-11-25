import { useEffect, useId, useState, type FormEvent } from 'react'
import { ChevronDown, RotateCcw, Save, Users } from 'lucide-react'
import { Button } from '../../components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from '../../components/ui/dialog'
import { courseService, type DesignSnapshot, type LearningDesign, type LearningDesignRole, type StageItem } from '../../services/api/course'
import { projectService } from '../../services/api/project'

const roles: { id: LearningDesignRole['id']; label: string; example: string }[] = [
  { id: 'evidence_researcher', label: '资料研究员', example: '示例：请帮助小组核对资料来源，指出还需要补充的证据。' },
  { id: 'viewpoint_challenger', label: '观点挑战者', example: '示例：请针对小组的解释提出反例，引导成员检验原有假设。' },
  { id: 'feedback_prompter', label: '反馈追问者', example: '示例：请基于小组当前论证提出一项具体的修改建议和一个追问。' },
  { id: 'problem_progressor', label: '问题推进者', example: '示例：请帮助小组明确当前问题，邀请成员提出下一步行动。' },
]

const stages: { id: StageItem['id']; label: string; example: string }[] = [
  { id: 'problem_construction', label: '问题构建', example: '示例：从任务情境中提出候选问题，与同伴商定共同探究的问题。' },
  { id: 'meaning_exploration', label: '意义探索', example: '示例：分工查找资料，记录来源并比较不同证据能够支持的解释。' },
  { id: 'explanation_integration', label: '解释整合', example: '示例：比较组内解释，整合可用证据并记录尚未解决的分歧。' },
  { id: 'application_solution', label: '应用解决', example: '示例：将共同解释用于设计方案，依据任务约束检验并修订方案。' },
]

const modes = {
  adaptive: '自适应协作',
  single: '单角色回应',
  parallel: '多角色并行',
  debate: '多角色辩论',
  pipeline: '多角色接力',
}
const field = 'w-full min-w-0 rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm placeholder:text-slate-500 focus:placeholder:text-transparent'
type Notice = { message: string; error?: boolean }

function defaultRole(id: LearningDesignRole['id']): LearningDesignRole {
  return { id, enabled: true, prompt: '' }
}

function editorValues(snapshot: DesignSnapshot | null): LearningDesign {
  return {
    roles: roles.map(({ id }) => {
      const saved = snapshot?.roles.find(role => role.id === id)
      return saved ? { id, enabled: saved.enabled, prompt: saved.prompt } : defaultRole(id)
    }),
    orchestration_mode: snapshot?.orchestration_mode ?? 'adaptive',
    stages: stages.map(({ id, label }) => {
      const saved = snapshot?.stages.find(stage => stage.id === id)
      return { id, name: saved?.name === label ? '' : saved?.name ?? '', activity_guidance: saved?.activity_guidance ?? '' }
    }),
  }
}

export default function CourseLearningDesign({ courseId }: { courseId: string }) {
  const panelId = useId()
  const [expanded, setExpanded] = useState(false)
  const [started, setStarted] = useState(false)

  return (
    <section className="border-b border-slate-200 pb-3">
      <h2>
        <button type="button" aria-expanded={expanded} aria-controls={panelId}
          className="flex w-full items-center justify-between gap-2 py-2 text-left font-semibold hover:text-indigo-700"
          onClick={() => { setExpanded(!expanded); setStarted(true) }}>
          班级学习设计 <ChevronDown size={18} aria-hidden="true" className={`shrink-0 ${expanded ? 'rotate-180' : ''}`} />
        </button>
      </h2>
      <div id={panelId} hidden={!expanded}>
        {started && <DesignEditor key={courseId} courseId={courseId} />}
      </div>
    </section>
  )
}

function DesignEditor({ courseId }: { courseId: string }) {
  const formId = useId()
  const [load, setLoad] = useState<'loading' | 'error' | 'ready'>('loading')
  const [attempt, setAttempt] = useState(0)
  const [saved, setSaved] = useState<DesignSnapshot | null>(null)
  const [draft, setDraft] = useState(() => editorValues(null))
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)
  const dirty = JSON.stringify(draft) !== JSON.stringify(editorValues(saved))
  const hasEnabledRole = draft.roles.some(role => role.enabled)
  const canSave = !saving && hasEnabledRole && (!saved || dirty)

  useEffect(() => {
    let active = true
    courseService.getLearningDesign(courseId).then(design => {
      if (!active) return
      setSaved(design)
      setDraft(editorValues(design))
      setLoad('ready')
    }, () => { if (active) setLoad('error') })
    return () => { active = false }
  }, [courseId, attempt])

  function edit(update: (current: LearningDesign) => LearningDesign) {
    setDraft(update)
    setNotice(null)
  }

  function editRole(id: LearningDesignRole['id'], patch: Partial<Omit<LearningDesignRole, 'id'>>) {
    edit(current => ({ ...current, roles: current.roles.map(role => role.id === id ? { ...role, ...patch } : role) }))
  }

  function editStage(id: StageItem['id'], patch: Partial<Omit<StageItem, 'id'>>) {
    edit(current => ({ ...current, stages: current.stages.map(stage => stage.id === id ? { ...stage, ...patch } : stage) }))
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canSave) return
    setSaving(true)
    setNotice(null)
    try {
      const design = await courseService.saveLearningDesign(courseId, {
        orchestration_mode: draft.orchestration_mode,
        // An explicit blank prompt uses the runtime default without copying system text into the editor.
        roles: draft.roles,
        stages: draft.stages.filter(stage => stage.name !== '' || stage.activity_guidance !== ''),
      })
      if (!design) throw new Error('Missing saved learning design')
      setSaved(design)
      setDraft(editorValues(design))
      setNotice({ message: '班级学习设计已保存，现有小组未更改。' })
    } catch {
      setNotice({ error: true, message: '未能确认保存学习设计，请重试。' })
    } finally {
      setSaving(false)
    }
  }

  if (load === 'loading') return <p role="status" className="py-2 text-sm text-slate-600">正在加载学习设计…</p>
  if (load === 'error') return (
    <div className="flex flex-wrap items-center gap-2 py-2">
      <p role="alert" className="text-sm text-red-700">无法加载学习设计。</p>
      <Button type="button" size="sm" variant="outline" onClick={() => {
        setLoad('loading')
        setAttempt(current => current + 1)
      }}>重试加载</Button>
    </div>
  )

  return (
    <div className="space-y-3 pb-2 pt-1">
      <form id={formId} onSubmit={save}>
        <fieldset disabled={saving} className="min-w-0 space-y-4">
          <fieldset className="min-w-0">
            <legend className="mb-2 text-sm font-semibold">AI 角色</legend>
            <div className="grid gap-x-5 gap-y-3 sm:grid-cols-2">
              {roles.map(({ id, label, example }, index) => {
                const role = draft.roles[index]
                return (
                  <div key={id} className="min-w-0 space-y-1">
                    <div className="flex items-center justify-between gap-2">
                      <label className="flex items-center gap-2 text-sm font-medium">
                        <input type="checkbox" checked={role.enabled} onChange={event => editRole(id, { enabled: event.target.checked })} />
                        {label}
                      </label>
                      <Button type="button" size="icon" variant="ghost" className="h-8 w-8 shrink-0"
                        title={`恢复${label}默认设置`} aria-label={`恢复${label}默认设置`}
                        disabled={role.enabled && !role.prompt} onClick={() => editRole(id, { enabled: true, prompt: '' })}>
                        <RotateCcw size={15} aria-hidden="true" />
                      </Button>
                    </div>
                    <textarea aria-label={`${label}提示词`} rows={3} maxLength={2500} className={field}
                      value={role.prompt} placeholder={example} onChange={event => editRole(id, { prompt: event.target.value })} />
                  </div>
                )
              })}
            </div>
          </fieldset>
          <fieldset className="min-w-0 border-t border-slate-200 pt-2">
            <legend className="pr-2 text-sm font-semibold">角色编排方式</legend>
            <div className="flex flex-wrap gap-x-4 gap-y-2">
              {(Object.keys(modes) as LearningDesign['orchestration_mode'][]).map(mode => (
                <label key={mode} className="flex items-center gap-1.5 text-sm">
                  <input type="radio" name="orchestration" checked={draft.orchestration_mode === mode}
                    onChange={() => edit(current => ({ ...current, orchestration_mode: mode }))} />
                  {modes[mode]}
                </label>
              ))}
            </div>
          </fieldset>
          <fieldset className="min-w-0 border-t border-slate-200 pt-2">
            <legend className="pr-2 text-sm font-semibold">学习阶段</legend>
            <div className="grid gap-x-5 gap-y-3 sm:grid-cols-2">
              {stages.map(({ id, label, example }, index) => (
                <div key={id} className="min-w-0 space-y-1">
                  <label className="block text-sm font-medium">{label} · 阶段名称
                    <input value={draft.stages[index].name} placeholder={label} maxLength={48} className={`${field} mt-1`}
                      onChange={event => editStage(id, { name: event.target.value })} />
                  </label>
                  <textarea aria-label={`${label}活动指导`} rows={3} maxLength={1600} className={field}
                    value={draft.stages[index].activity_guidance} placeholder={example}
                    onChange={event => editStage(id, { activity_guidance: event.target.value })} />
                </div>
              ))}
            </div>
          </fieldset>
        </fieldset>
      </form>
      {!hasEnabledRole && <p role="alert" className="text-sm text-red-700">请至少启用一个 AI 角色。</p>}
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" form={formId} size="sm" className="gap-1.5" disabled={!canSave}><Save size={15} aria-hidden="true" />{saving ? '正在保存…' : '保存学习设计'}</Button>
        <ApplyDesign courseId={courseId} enabled={!!saved && !dirty && !saving} onApplied={setNotice} />
        <span className="text-sm text-slate-600">{dirty ? '有未保存的修改' : !saved ? '尚未保存' : '已保存'}</span>
      </div>
      {notice && <p role={notice.error ? 'alert' : 'status'} className={`break-words text-sm ${notice.error ? 'text-red-700' : 'text-emerald-700'}`}>{notice.message}</p>}
    </div>
  )
}

function ApplyDesign({ courseId, enabled, onApplied }: { courseId: string; enabled: boolean; onApplied: (notice: Notice) => void }) {
  const [open, setOpen] = useState(false)
  const [applying, setApplying] = useState(false)

  return (
    <Dialog open={open} onOpenChange={next => { if (!applying && (!next || enabled)) setOpen(next) }}>
      <DialogTrigger asChild>
        <Button type="button" size="sm" variant="outline" className="gap-1.5" disabled={!enabled}><Users size={15} aria-hidden="true" />应用到小组</Button>
      </DialogTrigger>
      {open && <ApplyDesignDialog courseId={courseId} enabled={enabled} applying={applying} setApplying={setApplying}
        onCancel={() => setOpen(false)} onApplied={message => { setOpen(false); onApplied({ message }) }} />}
    </Dialog>
  )
}

function ApplyDesignDialog({ courseId, enabled, applying, setApplying, onCancel, onApplied }: {
  courseId: string
  enabled: boolean
  applying: boolean
  setApplying: (value: boolean) => void
  onCancel: () => void
  onApplied: (message: string) => void
}) {
  const [groups, setGroups] = useState<{ id: string; name: string }[]>([])
  const [projectId, setProjectId] = useState('')
  const [skip, setSkip] = useState(0)
  const [nextSkip, setNextSkip] = useState<number | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  const [applyError, setApplyError] = useState(false)
  const selected = groups.find(group => group.id === projectId)

  useEffect(() => {
    let active = true
    projectService.getProjects(false, skip).then(({ projects, total }) => {
      if (!active) return
      const matches = projects.filter(project => project.course_id === courseId && !project.is_archived && !project.is_template)
      setGroups(current => [...current, ...matches.filter(project => !current.some(group => group.id === project.id))])
      const offset = skip + projects.length
      setNextSkip(projects.length && offset < total ? offset : null)
      setLoading(false)
    }, () => {
      if (!active) return
      setLoadError(true)
      setLoading(false)
    })
    return () => { active = false }
  }, [courseId, skip, attempt])

  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!enabled || applying || !selected) return
    setApplying(true)
    setApplyError(false)
    try {
      const { revision } = await courseService.applyLearningDesign(courseId, selected.id)
      onApplied(`已将学习设计应用到「${selected.name}」（版本：${revision}）。`)
    } catch {
      setApplyError(true)
    } finally {
      setApplying(false)
    }
  }

  return (
    <DialogContent className="max-h-[90vh] w-[calc(100%_-_2rem)] overflow-y-auto rounded-lg sm:rounded-lg">
      <DialogTitle className="pr-6">应用学习设计到小组</DialogTitle>
      <DialogDescription>此操作会将最新已保存的班级学习设计应用到所选小组，替换该小组原有的学习设计。其他小组和其他设置不变。</DialogDescription>
      <form onSubmit={confirm} className="min-w-0 space-y-3">
        <fieldset disabled={applying} className="min-w-0 space-y-2">
          <label className="block text-sm font-medium">本班小组
            <select value={projectId} onChange={event => { setProjectId(event.target.value); setApplyError(false) }}
              className={`${field} mt-1`} disabled={!groups.length}>
              <option value="">请选择小组</option>
              {groups.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}
            </select>
          </label>
          {loading && <p role="status" className="text-sm text-slate-600">正在加载小组…</p>}
          {!loading && !loadError && !nextSkip && !groups.length && <p role="status" className="text-sm text-slate-600">本班暂无可应用的小组。</p>}
          {!loading && !loadError && nextSkip !== null && <Button type="button" variant="outline" size="sm" onClick={() => {
            setLoading(true)
            setSkip(nextSkip)
          }}>加载更多小组</Button>}
          {loadError && <div className="flex flex-wrap items-center gap-2">
            <p role="alert" className="text-sm text-red-700">无法加载小组。</p>
            <Button type="button" variant="outline" size="sm" onClick={() => {
              setLoading(true)
              setLoadError(false)
              setAttempt(current => current + 1)
            }}>重试加载小组</Button>
          </div>}
        </fieldset>
        {applyError && <p role="alert" className="text-sm text-red-700">未能确认应用学习设计。请检查小组状态后重试。</p>}
        <div className="flex flex-wrap justify-end gap-2">
          <Button type="button" size="sm" variant="outline" disabled={applying} onClick={onCancel}>取消</Button>
          <Button type="submit" size="sm" disabled={!enabled || applying || !selected}>{applying ? '正在应用…' : '确认应用'}</Button>
        </div>
      </form>
    </DialogContent>
  )
}
