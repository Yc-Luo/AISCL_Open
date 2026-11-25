// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { courseService, type CourseAISettings, type DesignSnapshot } from '../../services/api/course'
import { projectService } from '../../services/api/project'
import ClassManagement from '../../components/features/teacher/classmanagement/ClassManagement'
import TeacherClasses from './TeacherClasses'

vi.mock('../../stores/authStore', () => ({
  useAuthStore: (select: (state: { user: { id: string } }) => unknown) => select({ user: { id: 'me' } }),
}))
vi.mock('../../services/api/course', () => ({
  courseService: {
    getCourses: vi.fn(), getCourseStudents: vi.fn(),
    getCourseAISettings: vi.fn(), updateCourseAISettings: vi.fn(),
    getLearningDesign: vi.fn(), saveLearningDesign: vi.fn(), applyLearningDesign: vi.fn(),
    createCourse: vi.fn(), updateCourse: vi.fn(), getExperimentTemplates: vi.fn(),
  },
}))
vi.mock('../../services/api/project', () => ({ projectService: { getProjects: vi.fn() } }))

function open(path = '') {
  const course = { teacher_id: 'me', students: ['s1'], invite_code: '', created_at: '' }
  vi.mocked(courseService.getCourses).mockResolvedValue([
    { ...course, id: 'fall', name: '秋季班', semester: '2025 秋季' },
    { ...course, id: 'spring', name: '春季班', semester: '2026 春季' },
    { ...course, id: 'legacy', name: '往届班', semester: '' },
    { ...course, id: 'other', name: '其他教师的班', semester: '2026 春季', teacher_id: 'other' },
  ])
  vi.mocked(courseService.getCourseStudents).mockResolvedValue([
    { id: 's1', username: '学生甲', email: 'student@example.test' },
  ])
  vi.mocked(courseService.getCourseAISettings).mockResolvedValue(null)
  vi.mocked(courseService.getLearningDesign).mockResolvedValue(null)
  render(<MemoryRouter initialEntries={[`/teacher/overview${path}`]}>
    <Routes><Route path="/teacher/overview/:courseId?" element={<TeacherClasses />} /></Routes>
  </MemoryRouter>)
}

afterEach(() => { cleanup(); vi.clearAllMocks() })

it('filters owned courses and preserves view settings when returning from the roster', async () => {
  open()
  await screen.findByText('秋季班')
  expect(screen.queryByText('其他教师的班')).toBeNull()
  fireEvent.change(screen.getByLabelText('学年'), { target: { value: '未归入学年' } })
  fireEvent.change(screen.getByLabelText('学期'), { target: { value: '0' } })
  expect(screen.queryByText('春季班')).toBeNull()
  expect(screen.getByText('往届班')).toBeTruthy()
  fireEvent.change(screen.getByLabelText('学年'), { target: { value: '2025–2026' } })
  expect(screen.getByText('春季班')).toBeTruthy()
  fireEvent.change(screen.getByLabelText('学期'), { target: { value: '0' } })
  expect(screen.queryByText('秋季班')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '列表' }))
  fireEvent.change(screen.getByLabelText('密度'), { target: { value: 'compact' } })
  fireEvent.click(screen.getByRole('link', { name: /春季班/ }))
  await screen.findByText('学生甲')
  expect(courseService.getCourseStudents).toHaveBeenCalledWith('spring')
  fireEvent.click(screen.getByRole('link', { name: '返回我的班级' }))
  expect(screen.getByRole('button', { name: '列表' }).getAttribute('aria-pressed')).toBe('true')
  expect((screen.getByLabelText('密度') as HTMLSelectElement).value).toBe('compact')
  expect(screen.queryByText('秋季班')).toBeNull()
})

it('does not fetch the roster for a direct link to an unowned class', async () => {
  open('/other')
  await screen.findByText('找不到该班级，或该班级不属于当前账号。')
  expect(courseService.getCourseStudents).not.toHaveBeenCalled()
  expect(courseService.getCourseAISettings).not.toHaveBeenCalled()
  expect(courseService.getLearningDesign).not.toHaveBeenCalled()
})

it('pages the roster, clamps jumps and resets the page when the page size changes', async () => {
  open('/spring')
  vi.mocked(courseService.getCourseStudents).mockResolvedValueOnce(
    Array.from({ length: 106 }, (_, i) => ({ id: String(i), username: `成员${i + 1}`, email: '' })),
  )
  await screen.findByText('成员1')
  const prev = screen.getByRole<HTMLButtonElement>('button', { name: '上一页' })
  const next = screen.getByRole<HTMLButtonElement>('button', { name: '下一页' })
  const input = screen.getByRole<HTMLInputElement>('textbox', { name: '跳转页码' })
  const jump = (value: string) => {
    fireEvent.change(input, { target: { value } })
    fireEvent.click(screen.getByRole('button', { name: '跳转' }))
  }
  expect(prev.disabled).toBe(true)
  expect(screen.getAllByRole('listitem')).toHaveLength(10)
  fireEvent.click(next)
  expect(screen.getByText('成员11')).toBeTruthy()
  fireEvent.click(prev)
  expect(screen.getByText('成员1')).toBeTruthy()
  jump('3')
  expect(screen.getByText('成员21')).toBeTruthy()
  jump('999')
  expect(screen.getByText('成员106')).toBeTruthy()
  expect(next.disabled).toBe(true)
  jump('1.5')
  expect(input.value).toBe('11')
  jump('0')
  expect(input.value).toBe('1')
  jump('11')
  for (const size of [25, 50, 100]) {
    fireEvent.change(screen.getByLabelText('每页条数'), { target: { value: String(size) } })
    expect(input.value).toBe('1')
    expect(screen.getAllByRole('listitem')).toHaveLength(size)
  }
})

it('does not show pagination while the roster is loading or empty', async () => {
  open('/spring')
  vi.mocked(courseService.getCourseStudents).mockResolvedValueOnce([])
  expect(screen.queryByLabelText('每页条数')).toBeNull()
  await screen.findByText('暂无学生名单记录。')
  expect(screen.queryByLabelText('每页条数')).toBeNull()
  expect(screen.queryByLabelText('跳转页码')).toBeNull()
  expect(screen.queryByRole('button', { name: '下一页' })).toBeNull()
})

it('validates class AI settings and loads saved values without affecting other classes', async () => {
  open('/spring')
  const saved = new Map<string, CourseAISettings>()
  vi.mocked(courseService.getCourseAISettings).mockImplementation(async id => saved.get(id) ?? null)
  vi.mocked(courseService.updateCourseAISettings).mockImplementation(async (id, settings) => {
    saved.set(id, settings)
    return settings
  })

  const mode = await screen.findByLabelText<HTMLSelectElement>('AI 回答模式')
  const process = screen.getByLabelText<HTMLInputElement>('过程支架')
  const memory = screen.getByLabelText<HTMLInputElement>('共享记忆')
  const prompt = screen.getByLabelText<HTMLInputElement>('小组主动提示')
  const submit = () => fireEvent.click(screen.getByRole('button', { name: '保存 AI 支持设置' }))

  expect(screen.getByText(/尚未配置/)).toBeTruthy()
  expect(mode.value).toBe('single_agent')
  expect(process.checked).toBe(true)
  expect(memory.checked).toBe(false)
  expect(prompt.checked).toBe(false)
  expect(courseService.updateCourseAISettings).not.toHaveBeenCalled()

  fireEvent.click(memory)
  submit()
  expect(screen.getByRole('alert').textContent).toMatch(/仅在多智能体/)
  expect(courseService.updateCourseAISettings).not.toHaveBeenCalled()
  fireEvent.click(memory)
  fireEvent.click(prompt)
  submit()
  expect(screen.getByRole('alert').textContent).toMatch(/仅在多智能体/)
  expect(courseService.updateCourseAISettings).not.toHaveBeenCalled()

  fireEvent.change(mode, { target: { value: 'multi_agent' } })
  fireEvent.click(process)
  submit()
  expect(screen.getByRole('alert').textContent).toMatch(/需要开启过程支架/)
  expect(courseService.updateCourseAISettings).not.toHaveBeenCalled()

  fireEvent.click(prompt)
  fireEvent.click(memory)
  submit()
  await screen.findByText('班级 AI 支持设置已保存。')
  expect(courseService.updateCourseAISettings).toHaveBeenCalledTimes(1)
  expect(courseService.updateCourseAISettings).toHaveBeenCalledWith('spring', {
    ai_scaffold_mode: 'multi_agent',
    process_scaffold_mode: 'off',
    collaboration_memory_enabled: true,
    auto_prompt_enabled: false,
  })

  fireEvent.click(screen.getByRole('link', { name: '返回我的班级' }))
  fireEvent.click(screen.getByRole('link', { name: /秋季班/ }))
  expect((await screen.findByLabelText<HTMLSelectElement>('AI 回答模式')).value).toBe('single_agent')
  expect(screen.getByLabelText<HTMLInputElement>('共享记忆').checked).toBe(false)
  expect(screen.getByText(/尚未配置/)).toBeTruthy()
  fireEvent.click(screen.getByRole('link', { name: '返回我的班级' }))
  fireEvent.click(screen.getByRole('link', { name: /春季班/ }))
  expect((await screen.findByLabelText<HTMLSelectElement>('AI 回答模式')).value).toBe('multi_agent')
  expect(screen.getByLabelText<HTMLInputElement>('过程支架').checked).toBe(false)
  expect(screen.getByLabelText<HTMLInputElement>('共享记忆').checked).toBe(true)
  expect(screen.queryByText(/尚未配置/)).toBeNull()
})

it('creates and edits classes without selecting or submitting templates', async () => {
  vi.mocked(courseService.getCourses).mockResolvedValue([{
    id: 'legacy', name: '旧班级', teacher_id: 'me', semester: '2026 春季',
    invite_code: 'OLD', students: [], created_at: '2026-01-01', experiment_template_key: 'legacy-template',
  }])
  render(<MemoryRouter><ClassManagement /></MemoryRouter>)
  fireEvent.click(await screen.findByRole('button', { name: '创建新班级' }))
  const create = within(screen.getByRole('dialog', { name: '创建新班级' }))
  expect(create.queryByText(/模板/)).toBeNull()
  fireEvent.change(create.getByLabelText('班级名称'), { target: { value: '新班级' } })
  fireEvent.click(create.getByRole('button', { name: '确认创建' }))
  expect(courseService.createCourse).toHaveBeenCalledWith(expect.objectContaining({ name: '新班级' }))
  expect(vi.mocked(courseService.createCourse).mock.calls[0][0]).not.toHaveProperty('experiment_template_key')
  await screen.findByText(/班级已创建/)

  fireEvent.click(screen.getByRole('button', { name: '编辑班级 旧班级' }))
  const edit = within(screen.getByRole('dialog', { name: '修改班级信息' }))
  expect(edit.queryByText(/模板/)).toBeNull()
  fireEvent.click(edit.getByRole('button', { name: '确认修改' }))
  expect(courseService.updateCourse).toHaveBeenCalledWith('legacy', expect.objectContaining({ name: '旧班级' }))
  expect(vi.mocked(courseService.updateCourse).mock.calls[0][1]).not.toHaveProperty('experiment_template_key')
  expect(courseService.getExperimentTemplates).not.toHaveBeenCalled()
  await screen.findByText(/班级设置已更新/)
})

const existingDesign: DesignSnapshot = {
  revision: 'r1', orchestration_mode: 'debate',
  roles: [{ id: 'evidence_researcher', enabled: false, prompt: '教师自定义提示词' }],
  stages: [{ id: 'meaning_exploration', name: '证据比较', activity_guidance: '记录证据来源。' }],
}

it('opens the learning design on demand, edits sparse snapshots and restores a role without saving example text', async () => {
  open('/spring')
  vi.mocked(courseService.getLearningDesign).mockResolvedValue(existingDesign)
  vi.mocked(courseService.saveLearningDesign).mockImplementation(async (_, design) => ({ ...design, revision: 'r2' }))

  const section = await screen.findByRole('button', { name: '班级学习设计' })
  expect(section.getAttribute('aria-expanded')).toBe('false')
  expect(courseService.getLearningDesign).not.toHaveBeenCalled()
  fireEvent.click(section)
  const prompt = await screen.findByRole<HTMLTextAreaElement>('textbox', { name: '资料研究员提示词' })
  const missingPrompt = screen.getByRole<HTMLTextAreaElement>('textbox', { name: '观点挑战者提示词' })
  const apply = screen.getByRole<HTMLButtonElement>('button', { name: '应用到小组' })
  expect(prompt.value).toBe('教师自定义提示词')
  expect(missingPrompt.value).toBe('')
  expect(missingPrompt.placeholder).toMatch(/^示例/)
  expect(missingPrompt.maxLength).toBe(2500)
  expect(screen.getByRole<HTMLInputElement>('checkbox', { name: '资料研究员' }).checked).toBe(false)
  expect(screen.getByRole<HTMLInputElement>('checkbox', { name: '观点挑战者' }).checked).toBe(true)
  expect(screen.getByRole<HTMLInputElement>('radio', { name: '多角色辩论' }).checked).toBe(true)
  expect(screen.getByRole('group', { name: '角色编排方式' }).querySelectorAll('input')).toHaveLength(5)
  expect(screen.getByRole<HTMLInputElement>('textbox', { name: /意义探索.*阶段名称/ }).value).toBe('证据比较')
  expect(apply.disabled).toBe(false)

  fireEvent.click(screen.getByRole('button', { name: '恢复资料研究员默认设置' }))
  expect(prompt.value).toBe('')
  expect(screen.getByRole<HTMLInputElement>('checkbox', { name: '资料研究员' }).checked).toBe(true)
  fireEvent.click(section)
  fireEvent.click(section)
  expect(prompt.value).toBe('')
  expect(courseService.getLearningDesign).toHaveBeenCalledTimes(1)

  fireEvent.click(screen.getByRole('radio', { name: '多角色接力' }))
  const name = screen.getByRole<HTMLInputElement>('textbox', { name: /问题构建.*阶段名称/ })
  const guidance = screen.getByRole<HTMLTextAreaElement>('textbox', { name: '问题构建活动指导' })
  expect(name.placeholder).toBe('问题构建')
  expect(name.maxLength).toBe(48)
  expect(guidance.maxLength).toBe(1600)
  fireEvent.change(guidance, { target: { value: '提交小组问题。' } })
  expect(apply.disabled).toBe(true)
  fireEvent.click(apply)
  expect(projectService.getProjects).not.toHaveBeenCalled()

  fireEvent.click(screen.getByRole('button', { name: '保存学习设计' }))
  await screen.findByText('班级学习设计已保存，现有小组未更改。')
  expect(courseService.saveLearningDesign).toHaveBeenCalledWith('spring', {
    roles: ['evidence_researcher', 'viewpoint_challenger', 'feedback_prompter', 'problem_progressor']
      .map(id => ({ id, enabled: true, prompt: '' })),
    orchestration_mode: 'pipeline',
    stages: [
      { id: 'problem_construction', name: '', activity_guidance: '提交小组问题。' },
      existingDesign.stages[0],
    ],
  })
  expect(apply.disabled).toBe(false)
  expect(projectService.getProjects).not.toHaveBeenCalled()
  expect(courseService.applyLearningDesign).not.toHaveBeenCalled()
  expect(courseService.updateCourseAISettings).not.toHaveBeenCalled()
})

it('requires a confirmed save and explicit confirmation before applying to one eligible group on a later page', async () => {
  open('/spring')
  vi.mocked(courseService.saveLearningDesign).mockRejectedValueOnce(new Error('save failed'))
    .mockImplementation(async (_, design) => ({ ...design, revision: 'r2' }))
  vi.mocked(courseService.applyLearningDesign).mockRejectedValueOnce(new Error('apply failed'))
    .mockResolvedValueOnce({ project_id: 'target', revision: 'r2' })
  type Group = Awaited<ReturnType<typeof projectService.getProjects>>['projects'][number]
  const group = (id: string, overrides: Partial<Group> = {}): Group => ({
    id, name: id, owner_id: 'me', members: [], progress: 0, course_id: 'spring',
    is_template: false, is_archived: false, created_at: '', updated_at: '', ...overrides,
  })
  vi.mocked(projectService.getProjects).mockImplementation(async (_, skip) => ({
    projects: skip ? [group('target', { name: '本班活动小组' })] : [
      group('other', { course_id: 'fall' }), group('archived', { is_archived: true }), group('template', { is_template: true }),
    ],
    total: 4,
  }))

  fireEvent.click(await screen.findByRole('button', { name: '班级学习设计' }))
  const save = await screen.findByRole<HTMLButtonElement>('button', { name: '保存学习设计' })
  const apply = screen.getByRole<HTMLButtonElement>('button', { name: '应用到小组' })
  expect(apply.disabled).toBe(true)
  fireEvent.click(save)
  await screen.findByText('未能确认保存学习设计，请重试。')
  expect(apply.disabled).toBe(true)
  fireEvent.click(save)
  await screen.findByText('班级学习设计已保存，现有小组未更改。')
  fireEvent.click(apply)

  const dialog = within(await screen.findByRole('dialog', { name: '应用学习设计到小组' }))
  const loadMore = await dialog.findByRole('button', { name: '加载更多小组' })
  expect(dialog.getAllByRole('option').map(option => option.textContent)).toEqual(['请选择小组'])
  fireEvent.click(loadMore)
  await dialog.findByRole('option', { name: '本班活动小组' })
  expect(projectService.getProjects).toHaveBeenNthCalledWith(1, false, 0)
  expect(projectService.getProjects).toHaveBeenNthCalledWith(2, false, 3)
  const confirm = dialog.getByRole<HTMLButtonElement>('button', { name: '确认应用' })
  expect(confirm.disabled).toBe(true)
  fireEvent.change(dialog.getByLabelText('本班小组'), { target: { value: 'target' } })
  expect(courseService.applyLearningDesign).not.toHaveBeenCalled()
  fireEvent.click(confirm)
  await dialog.findByText(/未能确认应用学习设计/)
  fireEvent.click(confirm)
  await screen.findByText('已将学习设计应用到「本班活动小组」（版本：r2）。')
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(courseService.applyLearningDesign).toHaveBeenCalledTimes(2)
  expect(courseService.applyLearningDesign).toHaveBeenCalledWith('spring', 'target')
  expect(courseService.saveLearningDesign).toHaveBeenCalledTimes(2)
})

it('saves custom roles in order, reloads them, and saves removal without applying either revision', async () => {
  open('/spring')
  vi.mocked(courseService.getLearningDesign).mockResolvedValue(existingDesign)
  vi.mocked(courseService.saveLearningDesign).mockImplementation(async (_, design) => ({ ...design, revision: 'r2' }))
  fireEvent.click(await screen.findByRole('button', { name: '班级学习设计' }))
  const add = await screen.findByRole('button', { name: '添加自定义角色' })
  const save = screen.getByRole<HTMLButtonElement>('button', { name: '保存学习设计' })

  for (const [index, name] of ['  证据核验员  ', '来源核对员'].entries()) {
    fireEvent.click(add)
    const role = within(screen.getByRole('group', { name: `自定义角色 ${index + 1}` }))
    const anchor = role.getByRole<HTMLSelectElement>('combobox', { name: '路由类别' })
    fireEvent.change(anchor, { target: { value: 'viewpoint_challenger' } })
    expect(anchor.selectedOptions[0].textContent).toBe('观点挑战者')
    const prompt = role.getByRole<HTMLTextAreaElement>('textbox', { name: '提示词' })
    expect(prompt.value).toBe('')
    expect(prompt.placeholder).toMatch(/^示例/)
    expect(prompt.maxLength).toBe(2500)
    fireEvent.change(role.getByRole('textbox', { name: '角色名称' }), { target: { value: name } })
    fireEvent.change(prompt, { target: { value: `核对来源 ${index + 1}` } })
    if (index === 1) fireEvent.click(role.getByRole('checkbox', { name: '启用' }))
  }
  fireEvent.click(save)
  await screen.findByText('班级学习设计已保存，现有小组未更改。')
  const submitted = vi.mocked(courseService.saveLearningDesign).mock.calls[0][1]
  expect(submitted.roles.slice(0, 4)).toEqual([
    existingDesign.roles[0],
    { id: 'viewpoint_challenger', enabled: true, prompt: '' },
    { id: 'feedback_prompter', enabled: true, prompt: '' },
    { id: 'problem_progressor', enabled: true, prompt: '' },
  ])
  const [first, second] = submitted.roles.slice(4)
  expect(first).toEqual({
    id: expect.stringMatching(/^custom_[0-9a-f]{16}$/), name: '证据核验员', enabled: true,
    prompt: '核对来源 1', routing_anchor: 'viewpoint_challenger',
  })
  expect(second).toEqual({
    id: expect.stringMatching(/^custom_[0-9a-f]{16}$/), name: '来源核对员', enabled: false,
    prompt: '核对来源 2', routing_anchor: 'viewpoint_challenger',
  })
  expect(second.id).not.toBe(first.id)

  cleanup()
  open('/spring')
  vi.mocked(courseService.getLearningDesign).mockResolvedValue({ ...submitted, revision: 'r2' })
  vi.mocked(courseService.saveLearningDesign).mockImplementation(async (_, design) => ({ ...design, revision: 'r3' }))
  fireEvent.click(await screen.findByRole('button', { name: '班级学习设计' }))
  expect((await screen.findAllByRole<HTMLInputElement>('textbox', { name: '角色名称' })).map(input => input.value))
    .toEqual(['证据核验员', '来源核对员'])
  const firstRole = within(screen.getByRole('group', { name: '自定义角色 1' }))
  const secondRole = within(screen.getByRole('group', { name: '自定义角色 2' }))
  expect(firstRole.getByRole<HTMLTextAreaElement>('textbox', { name: '提示词' }).value).toBe('核对来源 1')
  expect(secondRole.getByRole<HTMLSelectElement>('combobox', { name: '路由类别' }).value).toBe('viewpoint_challenger')
  expect(secondRole.getByRole<HTMLInputElement>('checkbox', { name: '启用' }).checked).toBe(false)
  fireEvent.click(firstRole.getByRole('button', { name: '移除自定义角色' }))
  const apply = screen.getByRole<HTMLButtonElement>('button', { name: '应用到小组' })
  expect(apply.disabled).toBe(true)
  expect(courseService.saveLearningDesign).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: '保存学习设计' }))
  await screen.findByText('班级学习设计已保存，现有小组未更改。')
  expect(courseService.saveLearningDesign).toHaveBeenLastCalledWith('spring', {
    ...submitted, roles: [...submitted.roles.slice(0, 4), second],
  })
  expect(apply.disabled).toBe(false)
  expect(courseService.applyLearningDesign).not.toHaveBeenCalled()
  expect(projectService.getProjects).not.toHaveBeenCalled()
  expect(courseService.updateCourseAISettings).not.toHaveBeenCalled()
})

it('validates custom role names and prompts, enforces the cap, and counts enabled custom roles', async () => {
  open('/spring')
  fireEvent.click(await screen.findByRole('button', { name: '班级学习设计' }))
  const add = await screen.findByRole<HTMLButtonElement>('button', { name: '添加自定义角色' })
  const save = screen.getByRole<HTMLButtonElement>('button', { name: '保存学习设计' })
  for (let index = 0; index < 6; index++) fireEvent.click(add)
  expect(add.disabled).toBe(true)
  expect(screen.getByText('自定义角色 6/6')).toBeTruthy()
  const groups = screen.getAllByRole('group', { name: /^自定义角色/ })
  for (const group of groups.slice(2)) fireEvent.click(within(group).getByRole('button', { name: '移除自定义角色' }))
  expect(add.disabled).toBe(false)
  expect(save.disabled).toBe(true)

  const first = within(groups[0])
  const second = within(groups[1])
  const name = first.getByRole('textbox', { name: '角色名称' })
  const prompt = first.getByRole('textbox', { name: '提示词' })
  const change = (field: HTMLElement, value: string) => fireEvent.change(field, { target: { value } })
  change(second.getByRole('textbox', { name: '角色名称' }), 'Verifier')
  change(second.getByRole('textbox', { name: '提示词' }), '核验来源。')
  change(prompt, '核对证据。')
  for (const invalid of ['  ', '资料研究员', ' verifier ', '字'.repeat(41), '第一行\u2028第二行']) {
    change(name, invalid)
    expect(name.getAttribute('aria-invalid')).toBe('true')
    expect(save.disabled).toBe(true)
  }
  change(name, '  Checker  ')
  expect(save.disabled).toBe(false)
  for (const invalid of ['  ', '字'.repeat(2501)]) {
    change(prompt, invalid)
    expect(prompt.getAttribute('aria-invalid')).toBe('true')
    expect(save.disabled).toBe(true)
  }
  change(prompt, '字'.repeat(2500))
  for (const label of ['资料研究员', '观点挑战者', '反馈追问者', '问题推进者']) {
    fireEvent.click(screen.getByRole('checkbox', { name: label }))
  }
  expect(save.disabled).toBe(false)
  fireEvent.click(first.getByRole('checkbox', { name: '启用' }))
  fireEvent.click(second.getByRole('checkbox', { name: '启用' }))
  expect(save.disabled).toBe(true)
  expect(screen.getByText('请至少启用一个 AI 角色。')).toBeTruthy()
  expect(courseService.saveLearningDesign).not.toHaveBeenCalled()
})
