// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import api from '../../../../services/api/client'
import { courseService, type Course } from '../../../../services/api/course'
import { courseTaskReleaseService, type CourseTaskRelease as Release } from '../../../../services/api/courseTaskRelease'
import { storageService } from '../../../../services/api/storage'
import type { Resource, TeacherSubmission } from '../../../../types'
import CourseResource from '../courseresource/CourseResource'
import CourseTaskRelease from '../taskrelease/CourseTaskRelease'
import AssignmentReview from './AssignmentReview'

vi.mock('../../../../services/api/client', () => ({ default: { get: vi.fn() } }))
vi.mock('../../../../services/api/course', () => ({ courseService: { getCourses: vi.fn() } }))
vi.mock('../../../../services/api/courseTaskRelease', () => ({
  courseTaskReleaseService: { listCourseReleases: vi.fn(), createCourseRelease: vi.fn(), closeRelease: vi.fn() },
}))
vi.mock('../../../../services/api/storage', () => ({
  storageService: { getCourseResources: vi.fn(), uploadResourceFile: vi.fn(), deleteResource: vi.fn(), getResourceDownloadUrl: vi.fn() },
}))
vi.mock('../taskrelease/TaskBriefEditor', () => ({
  default: ({ onChange }: { onChange: (html: string, text: string) => void }) => (
    <textarea aria-label="项目说明编辑区" onChange={event => onChange(`<p>${event.target.value}</p>`, event.target.value)} />
  ),
}))

const spring: Course = { id: 'spring', name: '春季班', teacher_id: 'teacher', students: [], semester: '', invite_code: '', created_at: '' }
const fall: Course = { ...spring, id: 'fall', name: '秋季班' }
const resource: Resource = {
  id: 'resource-1', course_id: fall.id, filename: 'test.pdf', url: '', size: 1,
  mime_type: 'application/pdf', uploaded_by: 'teacher', uploaded_at: '2026-11-26T00:00:00Z', parse_status: 'indexed',
}
const release: Release = {
  id: 'release-1', course_id: spring.id, teacher_id: 'teacher', title: '新任务', allow_late_submission: true,
  status: 'open', target_project_ids: [], target_project_count: 0, synced_task_ids: [], synced_task_count: 0,
  synced_document_ids: [], synced_document_count: 0, submitted_count: 0, manual_submitted_count: 0,
  late_submitted_count: 0, auto_submitted_count: 0, created_by: 'teacher', created_at: '', updated_at: '',
  published_at: '2026-11-26T00:00:00Z',
}
const get = vi.mocked(api.get)
const getCourses = vi.mocked(courseService.getCourses)
const listResources = vi.mocked(storageService.getCourseResources)
const listReleases = vi.mocked(courseTaskReleaseService.listCourseReleases)

beforeEach(() => {
  getCourses.mockResolvedValue([spring, fall])
  get.mockResolvedValue({ data: { submissions: [] } })
  listResources.mockResolvedValue({ resources: [], total: 0 })
  listReleases.mockResolvedValue([])
})

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

it('uses the fixed class for listing and uploading resources and ignores a late response from the previous class', async () => {
  let resolveSpring!: (value: { resources: Resource[]; total: number }) => void
  listResources.mockImplementationOnce(() => new Promise(resolve => { resolveSpring = resolve }))
  const { rerender } = render(<CourseResource course={spring} />)
  expect(listResources).toHaveBeenCalledWith(spring.id, 'library')
  expect(screen.queryByRole('combobox')).toBeNull()
  rerender(<CourseResource course={fall} />)
  await screen.findByText('暂无班级资源')
  await act(async () => resolveSpring({ resources: [{ ...resource, filename: 'old.pdf' }], total: 1 }))
  expect(screen.queryByText('old.pdf')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'AI 知识库' }))
  await screen.findByText('暂无 AI 知识库资料')
  fireEvent.click(screen.getByRole('button', { name: '上传 AI 知识' }))
  const file = new File(['data'], 'test.pdf', { type: 'application/pdf' })
  fireEvent.change(screen.getByLabelText('选择文件'), { target: { files: [file] } })
  vi.mocked(storageService.uploadResourceFile).mockResolvedValue(resource)
  fireEvent.click(screen.getByRole('button', { name: '确认上传' }))
  await screen.findByText(/资料已上传/)
  expect(storageService.uploadResourceFile).toHaveBeenCalledWith({ course_id: fall.id, file, source_type: 'ai_knowledge' })
  expect(listResources).toHaveBeenLastCalledWith(fall.id, 'ai_knowledge')
  expect(getCourses).not.toHaveBeenCalled()
})

it('publishes and closes a class release without loading or offering other classes', async () => {
  vi.mocked(courseTaskReleaseService.createCourseRelease).mockResolvedValue(release)
  vi.mocked(courseTaskReleaseService.closeRelease).mockResolvedValue({ ...release, status: 'closed' })
  const { rerender } = render(<CourseTaskRelease course={spring} />)
  await screen.findByText('当前班级还没有发布过小组任务。')
  expect(listReleases).toHaveBeenCalledWith(spring.id)
  expect(screen.queryByRole('combobox')).toBeNull()
  fireEvent.change(screen.getByLabelText('任务标题'), { target: { value: release.title } })
  fireEvent.click(screen.getByRole('button', { name: '下一步' }))
  fireEvent.change(screen.getByLabelText('项目说明编辑区'), { target: { value: '讨论证据' } })
  fireEvent.click(screen.getByRole('button', { name: '下一步' }))
  fireEvent.click(screen.getByRole('button', { name: '发布到班级小组' }))
  const close = await screen.findByRole('button', { name: '关闭' })
  expect(courseTaskReleaseService.createCourseRelease).toHaveBeenCalledWith(spring.id, expect.objectContaining({ title: release.title, task_brief_html: '<p>讨论证据</p>' }))
  fireEvent.click(close)
  await screen.findByText('已关闭')
  expect(courseTaskReleaseService.closeRelease).toHaveBeenCalledWith(release.id)
  rerender(<CourseTaskRelease course={fall} />)
  expect(screen.queryByText(release.title)).toBeNull()
  await screen.findByText('当前班级还没有发布过小组任务。')
  expect(listReleases).toHaveBeenLastCalledWith(fall.id)
  expect(getCourses).not.toHaveBeenCalled()
})

it('uses server-side course and status filters for assignment review, with no scoped synchronous export controls', async () => {
  const submission: TeacherSubmission = {
    course_id: spring.id, course_name: spring.name, project_id: 'group', project_name: '春季班小组', artifacts: [], artifact_count: 0,
    task: { id: 'task', project_id: 'group', title: '本班任务', column: 'todo', priority: 'low', assignees: [], order: 1, created_at: '', updated_at: '' },
  }
  get.mockResolvedValueOnce({ data: { submissions: [submission] } })
  const { rerender } = render(<AssignmentReview courseId={spring.id} />)
  fireEvent.click(await screen.findByRole('button', { name: '查看审查' }))
  expect(screen.queryByRole('button', { name: '导出清单' })).toBeNull()
  expect(screen.queryByRole('button', { name: '下载全部成果' })).toBeNull()
  fireEvent.change(screen.getByRole('combobox', { name: '提交状态' }), { target: { value: 'submitted' } })
  await screen.findByText('暂无成果提交记录')
  expect(get).toHaveBeenLastCalledWith('/tasks/teacher/submissions', { params: { course_id: spring.id, status: 'submitted' } })
  rerender(<AssignmentReview courseId={fall.id} />)
  expect(screen.queryByText('本班任务')).toBeNull()
  await screen.findByText('暂无成果提交记录')
  expect(get).toHaveBeenLastCalledWith('/tasks/teacher/submissions', { params: { course_id: fall.id, status: undefined } })
  get.mockClear()
  rerender(<AssignmentReview courseId="" />)
  await screen.findByText('暂无成果提交记录')
  expect(get).not.toHaveBeenCalled()
})

it('preserves class selectors and the existing global export entry points when unscoped', async () => {
  render(<><CourseResource /><CourseTaskRelease /><AssignmentReview /></>)
  fireEvent.change(await screen.findByRole('combobox', { name: '当前班级' }), { target: { value: fall.id } })
  fireEvent.change(await screen.findByRole('combobox', { name: '发布班级' }), { target: { value: fall.id } })
  await waitFor(() => {
    expect(listResources).toHaveBeenLastCalledWith(fall.id, 'library')
    expect(listReleases).toHaveBeenLastCalledWith(fall.id)
  })
  expect(getCourses).toHaveBeenCalledTimes(2)
  expect(get).toHaveBeenCalledWith('/tasks/teacher/submissions', { params: { status: undefined } })
  expect(screen.getByRole('button', { name: '导出清单' })).toBeTruthy()
  expect(screen.getByRole('button', { name: '下载全部成果' })).toBeTruthy()
})
