import { describe, expect, it, vi } from 'vitest'
import { projectService } from '../services/api/project'
import type { Project } from '../types'
import { loadTeacherProjectList, teacherObservationLink, teacherObservationReturn, updateProjectListParams } from './teacherCourseNavigation'

vi.mock('../services/api/project', () => ({ projectService: { getProjects: vi.fn() } }))

const project = { id: 'a1', course_id: 'class-1' }
const readQuery = (url: string) => new URLSearchParams(url.slice(url.indexOf('?') + 1))

describe('teacher project list navigation', () => {
  it('keeps the course shell and unrelated params when changing filters', () => {
    const params = new URLSearchParams('section=members&panel=groups&year=2026&groupSearch=old&groupPage=2')
    expect(updateProjectListParams(params, { groupSearch: '物理', groupPage: '' }).toString())
      .toBe('section=members&panel=groups&year=2026&groupSearch=%E7%89%A9%E7%90%86')
    expect(params.get('groupPage')).toBe('2')
  })

  it('returns from management to the same class, groups panel, filters and page', () => {
    const params = new URLSearchParams('year=2026&term=fall&groupSearch=小组 A&groupStatus=completed&groupPage=3&course=other&unsafe=discard')
    const link = teacherObservationLink(project.id, 'manager', params, project.course_id)
    expect(link).toMatch(/^\/project\/a1\?/)
    const destination = teacherObservationReturn(readQuery(link), project)
    expect(destination.scoped).toBe(true)
    expect(destination.to).toMatch(/^\/teacher\/overview\/class-1\?/)
    expect(Object.fromEntries(readQuery(destination.to))).toEqual({
      section: 'members', panel: 'groups', year: '2026', term: 'fall', groupSearch: '小组 A',
      groupStatus: 'completed', groupPage: '3', group: project.id,
    })
  })

  it('returns from observation to the selected room and observation filters', () => {
    const params = new URLSearchParams('groupAttention=1&group=old&groupStatus=archived')
    const link = teacherObservationLink(project.id, 'monitor', params, project.course_id)
    const destination = teacherObservationReturn(readQuery(link), project)
    expect(destination.scoped).toBe(true)
    expect(Object.fromEntries(readQuery(destination.to))).toEqual({
      section: 'observe', group: project.id, groupStatus: 'archived', groupAttention: '1',
    })
  })

  it('canonicalizes an omitted management panel to groups', () => {
    const params = new URLSearchParams({ observe: 'manager', returnTo: '/teacher/overview/class-1?section=members' })
    expect(teacherObservationReturn(params, project)).toEqual({
      to: '/teacher/overview/class-1?section=members&panel=groups&group=a1', scoped: true,
    })
  })

  it.each([
    'https://external.example/teacher/overview/class-1?section=members&panel=groups',
    '//external.example/teacher/project-manager?section=members',
    '/teacher/overview/other?section=members&panel=groups',
    '/teacher/overview/class-1?section=members&panel=roster',
    '/teacher/overview/class-1?section=observe',
    '/teacher/project-manager?course=other',
    '/teacher/project-monitor?course=class-1',
  ])('falls back for an invalid manager return target: %s', (returnTo) => {
    expect(teacherObservationReturn(new URLSearchParams({ observe: 'manager', returnTo }), project)).toEqual({
      to: '/teacher/project-manager', scoped: false,
    })
  })

  it('supports direct legacy entry and preserves valid legacy filters', () => {
    const direct = new URLSearchParams('observe=monitor')
    expect(teacherObservationReturn(direct, project)).toEqual({ to: '/teacher/project-monitor', scoped: false })
    const params = new URLSearchParams('course=__unassigned__&groupSearch=alpha')
    const link = teacherObservationLink(project.id, 'monitor', params)
    expect(teacherObservationReturn(readQuery(link), { id: project.id })).toEqual({
      to: '/teacher/project-monitor?course=__unassigned__&groupSearch=alpha&group=a1', scoped: false,
    })
    expect(teacherObservationReturn(readQuery(link))).toEqual({ to: '/teacher/project-monitor', scoped: false })
  })
})

it('requests class-scoped pages and discards other classes from old backend responses', async () => {
  const projectApi = vi.mocked(projectService.getProjects)
  const valid = project as Project
  const other = { id: 'b1', course_id: 'other' } as Project
  projectApi.mockResolvedValueOnce({ projects: [valid, other], total: 3 })
  projectApi.mockResolvedValueOnce({ projects: [valid], total: 3 })
  await expect(loadTeacherProjectList(project.course_id)).resolves.toEqual([valid])
  expect(projectApi.mock.calls).toEqual([[undefined, 0, 'class-1'], [undefined, 2, 'class-1']])
})
