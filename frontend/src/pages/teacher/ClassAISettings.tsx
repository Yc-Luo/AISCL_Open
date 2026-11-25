import { useEffect, useState, type FormEvent } from 'react'
import { Button } from '../../components/ui/button'
import { courseService, type CourseAISettings } from '../../services/api/course'

const suggested: CourseAISettings = {
  ai_scaffold_mode: 'single_agent',
  process_scaffold_mode: 'on',
  collaboration_memory_enabled: false,
  auto_prompt_enabled: false,
}

function validationMessage(settings: CourseAISettings) {
  if (settings.ai_scaffold_mode !== 'multi_agent' &&
    (settings.collaboration_memory_enabled || settings.auto_prompt_enabled)) {
    return '共享记忆和小组主动提示仅在多智能体回答模式下可启用。'
  }
  if (settings.auto_prompt_enabled && settings.process_scaffold_mode !== 'on') {
    return '启用小组主动提示需要开启过程支架。'
  }
  return ''
}

export default function ClassAISettings({ courseId }: { courseId: string }) {
  const [load, setLoad] = useState<'loading' | 'error' | 'ready'>('loading')
  const [attempt, setAttempt] = useState(0)
  const [draft, setDraft] = useState(suggested)
  const [configured, setConfigured] = useState(false)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<{ error: boolean; message: string } | null>(null)

  useEffect(() => {
    let active = true
    courseService.getCourseAISettings(courseId).then(settings => {
      if (!active) return
      setDraft(settings ?? suggested)
      setConfigured(settings !== null)
      setLoad('ready')
    }, () => { if (active) setLoad('error') })
    return () => { active = false }
  }, [courseId, attempt])

  function update<K extends keyof CourseAISettings>(key: K, value: CourseAISettings[K]) {
    setDraft(current => ({ ...current, [key]: value }))
    setNotice(null)
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (load !== 'ready' || saving) return
    const invalid = validationMessage(draft)
    if (invalid) {
      setNotice({ error: true, message: invalid })
      return
    }

    setSaving(true)
    setNotice(null)
    try {
      const settings = await courseService.updateCourseAISettings(courseId, draft)
      if (!settings) throw new Error('Missing saved class AI settings')
      setDraft(settings)
      setConfigured(true)
      setNotice({ error: false, message: '班级 AI 支持设置已保存。' })
    } catch {
      setNotice({ error: true, message: '未能确认保存，请重试。' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <section aria-labelledby="class-ai-settings" className="space-y-3 border-b border-slate-200 pb-5">
      <h2 id="class-ai-settings" className="font-semibold">AI 支持设置</h2>
      <p className="text-sm text-slate-600">保存后仅对本班新建且选择继承班级设置的小组生效；已有小组请在各自的「小组仪表盘」调整。</p>
      {load === 'loading' && <p role="status" className="text-sm text-slate-600">正在加载 AI 支持设置…</p>}
      {load === 'error' && (
        <div className="flex flex-wrap items-center gap-2">
          <p role="alert" className="text-sm text-red-700">无法加载 AI 支持设置。</p>
          <Button type="button" variant="outline" size="sm" onClick={() => {
            setLoad('loading')
            setAttempt(current => current + 1)
          }}>重试 AI 设置</Button>
        </div>
      )}
      {load === 'ready' && (
        <form onSubmit={save} className="space-y-3">
          {!configured && <p className="text-sm text-slate-600">尚未配置。当前显示建议值，未保存。</p>}
          <fieldset disabled={saving} className="flex min-w-0 flex-wrap items-center gap-x-6 gap-y-3 text-sm">
            <label className="flex flex-wrap items-center gap-2">
              AI 回答模式
              <select className="rounded-md border border-slate-300 bg-white px-2 py-1.5"
                value={draft.ai_scaffold_mode}
                onChange={event => update('ai_scaffold_mode', event.target.value === 'multi_agent' ? 'multi_agent' : 'single_agent')}>
                <option value="single_agent">直接回答</option>
                <option value="multi_agent">多智能体</option>
              </select>
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={draft.process_scaffold_mode === 'on'}
                onChange={event => update('process_scaffold_mode', event.target.checked ? 'on' : 'off')} />
              过程支架
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={draft.collaboration_memory_enabled}
                onChange={event => update('collaboration_memory_enabled', event.target.checked)} />
              共享记忆
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={draft.auto_prompt_enabled}
                onChange={event => update('auto_prompt_enabled', event.target.checked)} />
              小组主动提示
            </label>
          </fieldset>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" size="sm" disabled={saving}>{saving ? '正在保存…' : '保存 AI 支持设置'}</Button>
            {notice && <p role={notice.error ? 'alert' : 'status'} className={`text-sm ${notice.error ? 'text-red-700' : 'text-emerald-700'}`}>
              {notice.message}
            </p>}
          </div>
        </form>
      )}
    </section>
  )
}
