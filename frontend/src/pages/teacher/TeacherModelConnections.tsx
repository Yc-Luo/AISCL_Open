import { useEffect, useId, useState, type FormEvent } from 'react'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { Button } from '../../components/ui/button'
import {
  teacherModelConnectionsService,
  type TeacherModelConnection,
  type TeacherModelProvider,
} from '../../services/api/teacherModelConnections'

const providers = {
  openai: { label: 'OpenAI', baseUrl: 'https://api.openai.com/v1', example: 'gpt-5.4-mini' },
  deepseek: { label: 'DeepSeek', baseUrl: 'https://api.deepseek.com', example: 'deepseek-flash' },
  siliconflow: { label: '硅基流动', baseUrl: 'https://api.siliconflow.cn/v1', example: 'deepseek-ai/DeepSeek-V4-Flash' },
  custom: { label: '自定义', baseUrl: '', example: '服务端提供的模型 ID' },
}

type Draft = {
  id?: string
  name: string
  provider: TeacherModelProvider
  model: string
  apiKey: string
  customUrl: string
}

const emptyDraft: Draft = { name: '', provider: 'openai', model: '', apiKey: '', customUrl: '' }
const fieldClass = 'min-w-0 w-full rounded-md border border-slate-300 bg-white px-2 py-1.5'

function validCustomUrl(value: string) {
  if (!/^https:\/\//i.test(value)) return false
  try {
    const url = new URL(value)
    return !!url.hostname && !url.port && !url.username && !url.password && !/[?#]/.test(value)
  } catch {
    return false
  }
}

export default function TeacherModelConnections({ onChanged }: { onChanged: () => void }) {
  const headingId = useId()
  const insecurePage = window.isSecureContext === false
  const [connections, setConnections] = useState<TeacherModelConnection[]>([])
  const [load, setLoad] = useState<'loading' | 'error' | 'ready'>('loading')
  const [attempt, setAttempt] = useState(0)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState<'saving' | 'deleting' | 'testing' | null>(null)
  const [notice, setNotice] = useState<{ error: boolean; message: string } | null>(null)

  useEffect(() => {
    let active = true
    teacherModelConnectionsService.list().then(items => {
      if (!active) return
      setConnections(items)
      setLoad('ready')
    }, () => { if (active) setLoad('error') })
    return () => { active = false }
  }, [attempt])

  function reload() {
    setLoad('loading')
    setAttempt(current => current + 1)
  }

  function openForm(connection?: TeacherModelConnection) {
    setNotice(null)
    setDraft(connection ? {
      id: connection.id,
      name: connection.name,
      provider: connection.provider,
      model: connection.model,
      apiKey: '',
      customUrl: connection.provider === 'custom' ? connection.base_url ?? '' : '',
    } : emptyDraft)
  }

  function update<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft(current => current && { ...current, [key]: value })
    setNotice(null)
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!draft || busy || insecurePage) return
    const { id, provider } = draft
    const name = draft.name.trim()
    const model = draft.model.trim()
    const apiKey = draft.apiKey.trim()
    const baseUrl = provider === 'custom' ? draft.customUrl.trim() : providers[provider].baseUrl
    const invalid = !name || !model ? '请输入连接名称和模型 ID。'
      : !id && !apiKey ? '请输入 API 密钥。'
      : provider === 'custom' && !validCustomUrl(baseUrl)
        ? '请输入有效的 HTTPS API 基础地址，不要包含账号、密码、查询参数或片段。'
        : ''
    if (invalid) {
      setNotice({ error: true, message: invalid })
      return
    }

    setBusy('saving')
    setNotice(null)
    const input = { name, provider, model, base_url: baseUrl }
    try {
      if (id) {
        await teacherModelConnectionsService.update(id, apiKey ? { ...input, api_key: apiKey } : input)
      } else {
        await teacherModelConnectionsService.create({ ...input, api_key: apiKey })
      }
    } catch {
      setNotice({ error: true, message: '保存失败。请检查模型 ID、密钥和接口基础地址后重试。' })
      return
    } finally {
      setBusy(null)
    }
    setDraft(null)
    setNotice({ error: false, message: '连接已保存。' })
    reload()
    onChanged()
  }

  async function remove(id: string) {
    if (busy || draft || !window.confirm('确定删除此连接？已应用此连接的小组可能暂时无法使用模型。')) return
    setBusy('deleting')
    setNotice(null)
    try {
      await teacherModelConnectionsService.remove(id)
    } catch {
      setNotice({ error: true, message: '删除失败。请刷新连接列表后重试。' })
      return
    } finally {
      setBusy(null)
    }
    setNotice({ error: false, message: '连接已删除。' })
    reload()
    onChanged()
  }

  async function test(id: string) {
    if (busy || draft) return
    setBusy('testing')
    setNotice(null)
    let success = false
    try {
      success = await teacherModelConnectionsService.test(id)
    } catch {
      // Provider errors may contain credentials; never render them.
    } finally {
      setBusy(null)
    }
    setNotice(success
      ? { error: false, message: '连接测试成功。' }
      : { error: true, message: '连接测试失败。请检查 API 密钥、模型 ID 和服务地址后重试。' })
  }

  return (
    <section aria-labelledby={headingId} className="space-y-3 border-b border-slate-200 pb-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id={headingId} className="font-semibold">个人模型连接</h2>
        {load === 'ready' && !draft && (
          <Button type="button" size="sm" disabled={!!busy || insecurePage} onClick={() => openForm()}>
            <Plus className="mr-1 size-4" aria-hidden="true" />新建连接
          </Button>
        )}
      </div>
      {insecurePage && <p role="status" className="text-sm text-slate-600">请通过 HTTPS 打开 AISCL 后再录入或更新 API 密钥。</p>}
      {load === 'loading' && <p role="status" className="text-sm text-slate-600">正在加载连接…</p>}
      {load === 'error' && (
        <div className="flex flex-wrap items-center gap-2">
          <p role="alert" className="text-sm text-red-700">无法加载连接列表。请检查网络后重试。</p>
          <Button type="button" variant="outline" size="sm" onClick={reload}>重试加载</Button>
        </div>
      )}
      {load === 'ready' && (connections.length ? (
        <ul className="divide-y divide-slate-200">
          {connections.map(connection => (
            <li key={connection.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <div className="min-w-0 break-all text-sm">
                <span className="font-medium">{connection.name}</span>
                <p className="text-slate-600">
                  {providers[connection.provider].label} · {connection.model} · {connection.has_api_key ? '已设置密钥' : '未设置密钥'}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant="outline" size="sm" disabled={!!busy || !!draft}
                  onClick={() => test(connection.id)}>测试连接</Button>
                <Button type="button" variant="outline" size="sm" disabled={!!busy || !!draft || insecurePage}
                  title="编辑连接" aria-label={`编辑连接：${connection.name}`} onClick={() => openForm(connection)}>
                  <Pencil className="size-4" aria-hidden="true" />
                </Button>
                <Button type="button" variant="outline" size="sm" disabled={!!busy || !!draft}
                  title="删除连接" aria-label={`删除连接：${connection.name}`} onClick={() => remove(connection.id)}>
                  <Trash2 className="size-4" aria-hidden="true" />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      ) : !draft && <p className="text-sm text-slate-600">尚未配置个人模型连接。</p>)}
      {draft && (
        <form onSubmit={save} className="space-y-3">
          <fieldset disabled={!!busy} className="grid min-w-0 gap-3 text-sm sm:grid-cols-2">
            <legend className="mb-2 font-medium">{draft.id ? '编辑连接' : '新建连接'}</legend>
            <label className="space-y-1">
              <span>连接名称</span>
              <input className={fieldClass} required value={draft.name}
                onChange={event => update('name', event.target.value)} />
            </label>
            <label className="space-y-1">
              <span>服务商</span>
              <select className={fieldClass} value={draft.provider} disabled={Boolean(draft.id)}
                onChange={event => update('provider', event.target.value as TeacherModelProvider)}>
                {Object.entries(providers).map(([value, { label }]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </label>
            <label className="space-y-1">
              <span>模型 ID</span>
              <input className={fieldClass} required value={draft.model} readOnly={Boolean(draft.id)}
                placeholder={providers[draft.provider].example}
                onChange={event => update('model', event.target.value)} />
            </label>
            <label className="space-y-1">
              <span>API 密钥{draft.id ? '（留空保留原密钥）' : ''}</span>
              <input className={fieldClass} type="password" autoComplete="new-password" required={!draft.id}
                value={draft.apiKey} onChange={event => update('apiKey', event.target.value)} />
            </label>
            <label className="space-y-1 sm:col-span-2">
              <span>API 基础地址</span>
              <input className={fieldClass} type="url" required readOnly={Boolean(draft.id) || draft.provider !== 'custom'}
                value={draft.provider === 'custom' ? draft.customUrl : providers[draft.provider].baseUrl}
                onChange={event => update('customUrl', event.target.value)} />
            </label>
          </fieldset>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" size="sm" disabled={!!busy}>{busy === 'saving' ? '正在保存…' : '保存连接'}</Button>
            <Button type="button" size="sm" variant="outline" disabled={!!busy} onClick={() => {
              setDraft(null)
              setNotice(null)
            }}>取消</Button>
          </div>
        </form>
      )}
      {busy === 'testing' && <p role="status" className="text-sm text-slate-600">正在测试连接…</p>}
      {busy === 'deleting' && <p role="status" className="text-sm text-slate-600">正在删除连接…</p>}
      {notice && (
        <p role={notice.error ? 'alert' : 'status'} className={`text-sm ${notice.error ? 'text-red-700' : 'text-emerald-700'}`}>
          {notice.message}
        </p>
      )}
    </section>
  )
}
