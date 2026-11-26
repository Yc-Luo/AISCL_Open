// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import api from '../../services/api/client'
import { courseService } from '../../services/api/course'
import type { ExportJob } from '../../services/api/courseExports'
import CourseExports from './CourseExports'
import TeacherClasses from './TeacherClasses'

vi.mock('../../services/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), getUri: () => 'https://api.example.test/api/v1' },
}))
const auth = vi.hoisted(() => ({ user: { id: 'owner' } }))
vi.mock('../../stores/authStore', () => ({
  useAuthStore: (select: (state: typeof auth) => unknown) => select(auth),
}))
vi.mock('../../services/api/course', () => ({
  courseService: { getCourses: vi.fn(), getCourseStudents: vi.fn() },
}))
vi.mock('./ClassAISettings', () => ({ default: () => null }))
vi.mock('./CourseLearningDesign', () => ({ default: () => null }))

const base = '/courses/spring/export-jobs'
const groupPath = '/courses/spring/export-groups'
const get = vi.mocked(api.get)
const post = vi.mocked(api.post)
const groups = [
  { id: 'g1', name: '在用小组', is_archived: false },
  { id: 'g2', name: '旧小组', is_archived: true },
]
const job = (overrides: Partial<ExportJob> = {}): ExportJob => ({
  id: 'job1', profile: 'participation', status: 'queued', course_id: 'spring', project_ids: [],
  from_date: null, through_date: null, stage_id: null, include_files: false, progress: 0,
  message: '', filename: null, file_size: 0, missing_files: 0, created_at: '2026-11-26T00:00:00Z',
  ...overrides,
})
const toggle = () => fireEvent.click(screen.getByRole('button', { name: '成果与导出' }))
const change = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })
const createButton = () => screen.getByRole<HTMLButtonElement>('button', { name: '创建导出' })
const anySignal = { signal: expect.any(AbortSignal) }

function history(jobs: ExportJob[] = []) {
  get.mockImplementation(async path => {
    if (path === groupPath) return { data: { groups } }
    if (path === base) return { data: { jobs } }
    throw new Error(`Unexpected GET ${path}`)
  })
}

async function openPanel() {
  render(<CourseExports courseId="spring" />)
  toggle()
  await screen.findByRole('button', { name: '创建导出' })
}

beforeEach(() => {
  history()
  post.mockResolvedValue({ data: job() })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.resetAllMocks()
})

it('loads lazily and retrieves server history when returning to the class', async () => {
  const { unmount } = render(<CourseExports courseId="spring" />)
  expect(screen.getByRole('button', { name: '成果与导出' }).getAttribute('aria-expanded')).toBe('false')
  expect(get).not.toHaveBeenCalled()
  toggle()
  await screen.findByText('暂无导出任务。')
  expect(get).toHaveBeenNthCalledWith(1, groupPath, anySignal)
  expect(get).toHaveBeenNthCalledWith(2, base, anySignal)

  fireEvent.click(createButton())
  await screen.findByText('· 排队中')
  expect(post).toHaveBeenCalledWith(base, { profile: 'participation' }, anySignal)
  unmount()

  history([job({ status: 'completed', filename: 'participation.zip' })])
  await openPanel()
  expect(screen.getByText('participation.zip')).toBeTruthy()
  expect(get).toHaveBeenCalledTimes(4)
})

it('validates ranges and only submits filters supported by the selected profile', async () => {
  await openPanel()
  change('导出类型', 'research')
  change('小组范围', 'selected')
  expect(createButton().disabled).toBe(true)
  fireEvent.click(screen.getByRole('checkbox', { name: '在用小组' }))
  fireEvent.click(screen.getByRole('checkbox', { name: '旧小组（已归档）' }))
  change('开始日期', '2026-11-10')
  change('截止日期（含当日）', '2026-11-09')
  expect(createButton().disabled).toBe(true)
  expect(screen.getByRole('alert').textContent).toBe('截止日期不能早于开始日期。')
  expect(post).not.toHaveBeenCalled()
  change('截止日期（含当日）', '2026-11-10')
  const stage = screen.getByRole('combobox', { name: '学习阶段' })
  expect(within(stage).getAllByRole<HTMLOptionElement>('option').map(option => option.value))
    .toEqual(['', 'problem_construction', 'meaning_exploration', 'explanation_integration', 'application_solution'])
  change('学习阶段', 'explanation_integration')
  fireEvent.click(createButton())
  await waitFor(() => expect(createButton().disabled).toBe(false))
  expect(post).toHaveBeenLastCalledWith(base, {
    profile: 'research', project_ids: ['g1', 'g2'], from_date: '2026-11-10', through_date: '2026-11-10',
    stage_id: 'explanation_integration',
  }, anySignal)

  change('导出类型', 'artifacts')
  change('小组范围', 'all')
  change('开始日期', '')
  expect(screen.queryByLabelText('学习阶段')).toBeNull()
  fireEvent.click(screen.getByRole('checkbox', { name: '包含附件' }))
  fireEvent.click(createButton())
  await waitFor(() => expect(createButton().disabled).toBe(false))
  expect(post).toHaveBeenLastCalledWith(base, {
    profile: 'artifacts', through_date: '2026-11-10', include_files: true,
  }, anySignal)

  change('导出类型', 'participation')
  expect(screen.queryByRole('checkbox')).toBeNull()
  fireEvent.click(createButton())
  await waitFor(() => expect(createButton().disabled).toBe(false))
  expect(post).toHaveBeenLastCalledWith(base, { profile: 'participation', through_date: '2026-11-10' }, anySignal)
})

it('polls queued and running jobs, recovers from a transient error and stops at completion', async () => {
  vi.useFakeTimers()
  history([job()])
  render(<CourseExports courseId="spring" />)
  toggle()
  await act(async () => {})
  get.mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValueOnce({ data: job({ status: 'running', progress: 35 }) })
    .mockResolvedValueOnce({ data: job({ status: 'completed', progress: 100, filename: 'data.zip', file_size: 2048, missing_files: 2 }) })

  for (const text of ['暂时无法更新部分任务状态，正在重试。', '· 生成中', '· 已完成']) {
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(screen.getByText(text)).toBeTruthy()
    if (text === '· 生成中') expect(screen.getByRole<HTMLProgressElement>('progressbar').value).toBe(35)
  }
  expect(get).toHaveBeenLastCalledWith(`${base}/job1`, anySignal)
  expect(screen.queryByRole('progressbar')).toBeNull()
  expect(screen.queryByText(/暂时无法更新/)).toBeNull()
  expect(screen.getByText('data.zip · 2.0 KB')).toBeTruthy()
  expect(screen.getByText('缺少 2 个附件')).toBeTruthy()
  const count = get.mock.calls.length
  await act(async () => { await vi.advanceTimersByTimeAsync(9000) })
  expect(get).toHaveBeenCalledTimes(count)
})

it('retries failed jobs and regenerates expired jobs with their original options', async () => {
  history([
    job({ status: 'failed', profile: 'research', stage_id: 'application_solution', from_date: '2026-11-02', project_ids: ['g2'], message: '生成中断' }),
    job({ id: 'old-job', status: 'expired', profile: 'artifacts', include_files: true, through_date: '2026-11-26' }),
  ])
  post.mockResolvedValue({ data: job({ id: 'new-job' }) })
  await openPanel()
  expect(screen.getByText('生成中断')).toBeTruthy()
  expect(screen.queryByRole('button', { name: '下载' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '重试' }))
  await waitFor(() => expect(createButton().disabled).toBe(false))
  expect(post).toHaveBeenLastCalledWith(base, {
    profile: 'research', stage_id: 'application_solution', from_date: '2026-11-02', project_ids: ['g2'],
  }, anySignal)
  fireEvent.click(screen.getByRole('button', { name: '重新生成' }))
  await waitFor(() => expect(createButton().disabled).toBe(false))
  expect(post).toHaveBeenLastCalledWith(base, { profile: 'artifacts', include_files: true, through_date: '2026-11-26' }, anySignal)
})

it('opens a fresh credentialed ticket via a native link on the API origin', async () => {
  const links: HTMLAnchorElement[] = []
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    links.push(this)
  })
  try {
    history([job({ id: 'id/1', status: 'completed', filename: 'result.zip' })])
    post.mockResolvedValueOnce({ data: { download_url: '/api/v1/courses/spring/download', expires_at: '2026-11-26' } })
      .mockResolvedValueOnce({ data: { download_url: '/courses/spring/download', expires_at: '2026-11-26' } })
      .mockResolvedValueOnce({ data: { download_url: 'https://elsewhere.example/api/v1/download', expires_at: '2026-11-26' } })
    await openPanel()
    const download = screen.getByRole('button', { name: '下载' })
    for (let i = 0; i < 2; i++) {
      fireEvent.click(download)
      await waitFor(() => expect(click).toHaveBeenCalledTimes(i + 1))
      expect(post).toHaveBeenLastCalledWith(`${base}/id%2F1/download-ticket`, undefined, { ...anySignal, withCredentials: true })
      const link = links[i]
      expect(link.href).toBe('https://api.example.test/api/v1/courses/spring/download')
      expect(link.download).toBe('result.zip')
    }
    fireEvent.click(download)
    await screen.findByText(/无法下载文件/)
    expect(click).toHaveBeenCalledTimes(2)
  } finally {
    click.mockRestore()
  }
})

it('aborts in-flight polling on class switch and ignores late results', async () => {
  vi.useFakeTimers()
  history([job()])
  const { rerender } = render(<CourseExports courseId="spring" />)
  toggle()
  await act(async () => {})
  let resolvePoll!: (value: { data: ExportJob }) => void
  get.mockImplementationOnce(() => new Promise(resolve => { resolvePoll = resolve }))
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  const signal = get.mock.calls[2][1]?.signal
  expect(signal?.aborted).toBe(false)

  get.mockResolvedValueOnce({ data: { groups: [] } }).mockResolvedValueOnce({ data: { jobs: [] } })
  rerender(<CourseExports courseId="fall" />)
  expect(signal?.aborted).toBe(true)
  toggle()
  await act(async () => { resolvePoll({ data: job({ status: 'completed', filename: 'old.zip' }) }) })
  expect(screen.getByText('暂无导出任务。')).toBeTruthy()
  expect(screen.queryByText('old.zip')).toBeNull()
  expect(get).toHaveBeenLastCalledWith('/courses/fall/export-jobs', anySignal)
})

it('does not navigate when a download ticket arrives after closing the panel', async () => {
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  try {
    history([job({ status: 'completed', filename: 'result.zip' })])
    let resolveTicket!: (value: { data: { download_url: string; expires_at: string } }) => void
    post.mockImplementationOnce(() => new Promise(resolve => { resolveTicket = resolve }))
    await openPanel()
    fireEvent.click(screen.getByRole('button', { name: '下载' }))
    const signal = post.mock.calls[0][2]?.signal
    toggle()
    expect(signal?.aborted).toBe(true)
    await act(async () => { resolveTicket({ data: { download_url: '/api/v1/download', expires_at: '2026-11-26' } }) })
    expect(click).not.toHaveBeenCalled()
  } finally {
    click.mockRestore()
  }
})

it('clears the form and history when an action loses class export access', async () => {
  history([job({ status: 'completed', filename: 'result.zip' })])
  post.mockRejectedValueOnce({ isAxiosError: true, response: { status: 403 } })
  await openPanel()
  fireEvent.click(createButton())
  await screen.findByText('登录已失效或没有本班导出权限。')
  expect(screen.queryByText('result.zip')).toBeNull()
  expect(screen.queryByRole('button', { name: '创建导出' })).toBeNull()
  expect(post.mock.calls[0][2]?.signal?.aborted).toBe(true)
})

it('explains a create conflict and refreshes a completed job when its file has expired', async () => {
  history([job({ status: 'completed', filename: 'result.zip' })])
  const conflict = { isAxiosError: true, response: { status: 409 } }
  post.mockRejectedValueOnce(conflict).mockRejectedValueOnce(conflict)
  await openPanel()

  fireEvent.click(createButton())
  await screen.findByText('已有导出任务正在进行，请等待完成后再生成。')
  get.mockResolvedValueOnce({ data: job({ status: 'expired', filename: 'result.zip' }) })
  fireEvent.click(screen.getByRole('button', { name: '下载' }))
  await screen.findByText('· 已过期')
  expect(get).toHaveBeenLastCalledWith(`${base}/job1`, anySignal)
  expect(screen.queryByRole('button', { name: '下载' })).toBeNull()
  expect(screen.getByRole<HTMLButtonElement>('button', { name: '重新生成' }).disabled).toBe(false)
})

it('exposes exports only under owned class details and cancels requests on account switch', async () => {
  vi.mocked(courseService.getCourses).mockResolvedValue([
    { id: 'spring', name: '春季班', teacher_id: 'owner', students: [], semester: '', invite_code: '', created_at: '' },
  ])
  vi.mocked(courseService.getCourseStudents).mockResolvedValue([])
  const classPage = () => (
    <MemoryRouter initialEntries={['/teacher/overview/spring']}>
      <Routes><Route path="/teacher/overview/:courseId" element={<TeacherClasses />} /></Routes>
    </MemoryRouter>
  )
  const { rerender } = render(classPage())
  await screen.findByText('班级详情')
  expect(get).not.toHaveBeenCalled()
  let resolveGroups!: (value: { data: { groups: typeof groups } }) => void
  get.mockImplementationOnce(() => new Promise(resolve => { resolveGroups = resolve }))
  toggle()
  const signal = get.mock.calls[0][1]?.signal
  try {
    auth.user = { id: 'other' }
    rerender(classPage())
    expect(signal?.aborted).toBe(true)
    await act(async () => { resolveGroups({ data: { groups } }) })
    expect(screen.getByText('找不到该班级，或该班级不属于当前账号。')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '成果与导出' })).toBeNull()
    expect(get).toHaveBeenCalledTimes(2)
  } finally {
    auth.user = { id: 'owner' }
  }
})
