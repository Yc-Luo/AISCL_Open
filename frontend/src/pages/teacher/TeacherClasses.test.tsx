// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { courseService } from '../../services/api/course'
import TeacherClasses from './TeacherClasses'

vi.mock('../../stores/authStore', () => ({
  useAuthStore: (select: (state: { user: { id: string } }) => unknown) => select({ user: { id: 'me' } }),
}))
vi.mock('../../services/api/course', () => ({
  courseService: { getCourses: vi.fn(), getCourseStudents: vi.fn() },
}))

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
