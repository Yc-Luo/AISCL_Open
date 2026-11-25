// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { create } from 'zustand'
import { useAuthStore } from '../../stores/authStore'
import { useContextStore } from '../../stores/contextStore'
import ProjectList from './ProjectList'
import ProjectWorkspace from './ProjectWorkspace'

const mocks = vi.hoisted(() => ({
  getProjects: vi.fn(), getProject: vi.fn(), getExperimentVersion: vi.fn(),
  getCourses: vi.fn(), getDocuments: vi.fn(), createDocument: vi.fn(),
  joinRoom: vi.fn(), leaveRoom: vi.fn(),
}))

vi.mock('../../stores/authStore', () => ({ useAuthStore: create(() => ({ user: null })) }))
vi.mock('../../services/api/project', () => ({ projectService: mocks }))
vi.mock('../../services/api/course', () => ({ courseService: mocks }))
vi.mock('../../services/api/document', () => ({ documentService: mocks }))
vi.mock('../../services/sync/SyncService', () => ({
  syncService: {
    init: async () => {}, ...mocks, on: () => {}, off: () => {},
    sendEphemeralOperation: async () => {},
  },
}))
vi.mock('../../hooks/common/useActivityTracking', () => ({ useActivityTracking: () => {} }))
vi.mock('../../hooks/common/useBehaviorTracking', () => ({ useBehaviorTracking: () => {} }))
vi.mock('../../services/tracking/TrackingService', () => ({
  trackingService: { trackResearchEvent: () => {} },
}))
vi.mock('../../components/layout/Sidebar', () => ({ default: () => null }))
vi.mock('../../components/layout/RightSidebar', () => ({ default: () => null }))
vi.mock('../../components/layout/TabNavigation', () => ({ default: () => null }))
vi.mock('../../components/features/student/resources/ResourceLibrary', () => ({ default: () => null }))
vi.mock('../../components/features/student/wiki/ProjectWiki', () => ({ default: () => null }))
vi.mock('../../components/features/student/dashboard/LearningDashboard', () => ({ default: () => null }))
vi.mock('../../components/features/student/document/DocumentEditor', () => ({ default: () => null }))
vi.mock('../../modules/inquiry/components/InquirySpace', () => ({ InquirySpace: () => null }))
vi.mock('../../components/features/student/ai/AITutor', () => ({ default: () => null }))
vi.mock('../../components/features/student/ai/ContextualAIAssistant', () => ({ default: () => null }))
vi.mock('../../components/features/student/settings/Settings', () => ({ default: () => null }))
vi.mock('../../components/ui', () => ({ ConfirmDialog: () => null, useToast: () => ({}) }))

const account = (id: string) => ({ id, role: 'student' as const, username: id, email: id + '@example.test' })
const project = (id: string, is_archived = false) => ({ id, name: id, members: [], is_archived })
const deferred = <T,>() => {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

function TestRoutes() {
  const navigate = useNavigate()
  return <>
    <button onClick={() => navigate('/project/group-b')}>切换测试小组</button>
    <Routes>
      <Route path="/student/dashboard" element={<ProjectWorkspace />} />
      <Route path="/student/projects" element={<ProjectList />} />
      <Route path="/project/:projectId" element={<ProjectWorkspace />} />
    </Routes>
  </>
}

const open = (path: string) => render(<MemoryRouter initialEntries={[path]}><TestRoutes /></MemoryRouter>)

beforeEach(() => {
  vi.resetAllMocks()
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener() {}, removeEventListener() {} }))
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => key === 'aiscl:student-onboarding-dismissed' ? 'true' : null,
    setItem: () => {},
  })
  useAuthStore.setState({ user: account('student-a') as NonNullable<ReturnType<typeof useAuthStore.getState>['user']> })
  mocks.getProjects.mockResolvedValue({ projects: [], total: 0 })
  mocks.getCourses.mockResolvedValue([])
  mocks.getExperimentVersion.mockResolvedValue({})
  mocks.getDocuments.mockResolvedValue({ documents: [] })
  mocks.joinRoom.mockResolvedValue(undefined)
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('student project context', () => {
  it('uses the list for an ungrouped student, without starting a workspace', async () => {
    open('/student/dashboard')
    expect(await screen.findByText('还没有小组')).toBeTruthy()
    expect(mocks.getProject).not.toHaveBeenCalled()
    expect(mocks.joinRoom).not.toHaveBeenCalled()
  })

  it('shows archived groups from later pages and enters only on selection, without creating a document', async () => {
    mocks.getProjects.mockResolvedValueOnce({ projects: [project('active')], total: 2 })
      .mockResolvedValueOnce({ projects: [project('archived', true)], total: 2 })
    mocks.getProject.mockResolvedValue(project('archived', true))
    open('/student/dashboard')
    const archived = await screen.findByRole('link', { name: /archived 已归档/ })
    expect(mocks.getProjects).toHaveBeenLastCalledWith(undefined, 1)
    expect(mocks.getProject).not.toHaveBeenCalled()
    fireEvent.click(archived)
    await waitFor(() => expect(mocks.getDocuments).toHaveBeenCalledWith('archived', 0, 1))
    expect(mocks.createDocument).not.toHaveBeenCalled()
  })

  it('does not apply a late list response from another student', async () => {
    const previous = deferred<{ projects: ReturnType<typeof project>[], total: number }>()
    mocks.getProjects.mockReturnValueOnce(previous.promise)
    open('/student/projects')
    act(() => useAuthStore.setState({ user: { ...useAuthStore.getState().user!, id: 'student-b' } }))
    expect(await screen.findByText('还没有小组')).toBeTruthy()
    await act(async () => previous.resolve({ projects: [project('old-group')], total: 1 }))
    expect(screen.queryByText('old-group')).toBeNull()
  })

  it('requires explicit course selection before creating a group', async () => {
    mocks.getCourses.mockResolvedValue([{ id: 'course', name: '测试课程', semester: '2026' }])
    open('/student/projects')
    const openForm = await screen.findByRole<HTMLButtonElement>('button', { name: '创建小组' })
    await waitFor(() => expect(openForm.disabled).toBe(false))
    fireEvent.click(openForm)
    const select = screen.getByRole<HTMLSelectElement>('combobox')
    expect(select.value).toBe('')
    const submit = screen.getAllByRole<HTMLButtonElement>('button', { name: '创建小组' })[1]
    expect(submit.disabled).toBe(true)
    fireEvent.change(select, { target: { value: 'course' } })
    expect(submit.disabled).toBe(false)
  })

  it('drops a late project response on group change and clears the authorized context on account change', async () => {
    const previous = deferred<ReturnType<typeof project>>()
    mocks.getProject.mockReturnValueOnce(previous.promise).mockResolvedValueOnce(project('group-b'))
      .mockRejectedValueOnce({ response: { status: 403 } })
    open('/project/group-a')
    expect(mocks.joinRoom).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText('切换测试小组'))
    await waitFor(() => expect(useContextStore.getState().projectId).toBe('group-b'))
    await act(async () => previous.resolve(project('group-a')))
    expect(mocks.getExperimentVersion).not.toHaveBeenCalledWith('group-a')
    act(() => useAuthStore.setState({ user: { ...useAuthStore.getState().user!, id: 'student-b' } }))
    expect(await screen.findByText('该小组不存在，或当前账号无权访问。')).toBeTruthy()
    expect(useContextStore.getState().projectId).toBeNull()
    expect(mocks.leaveRoom).toHaveBeenCalledWith('project:group-b', 'chat')
    expect(mocks.joinRoom).toHaveBeenCalledTimes(1)
  })
})
