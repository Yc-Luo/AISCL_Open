import { ROUTES } from '../config/routes'
import { projectService } from '../services/api/project'
import type { Project } from '../types'

type ObservationSource = 'manager' | 'monitor'
const classShellKeys = ['year', 'term', 'view', 'density'] as const
export const teacherProjectSubviewKeys = [
  'course', 'groupSearch', 'groupStatus', 'groupAttention', 'group', 'groupPage',
] as const

export function updateProjectListParams(
  current: URLSearchParams,
  changes: Partial<Record<typeof teacherProjectSubviewKeys[number], string>>,
): URLSearchParams {
  const next = new URLSearchParams(current)
  for (const [key, value] of Object.entries(changes)) {
    if (value) next.set(key, value)
    else next.delete(key)
  }
  return next
}

function legacyPath(source: ObservationSource) {
  return source === 'manager' ? ROUTES.TEACHER.PROJECT_MANAGER : ROUTES.TEACHER.PROJECT_MONITOR
}

function overviewPath(courseId: string) {
  return `${ROUTES.TEACHER.OVERVIEW}/${encodeURIComponent(courseId)}`
}

function listDestination(source: ObservationSource, params: URLSearchParams, projectId: string, courseId?: string) {
  const next = new URLSearchParams()
  if (courseId) {
    next.set('section', source === 'manager' ? 'members' : 'observe')
    if (source === 'manager') next.set('panel', 'groups')
  }
  for (const key of [...classShellKeys, ...teacherProjectSubviewKeys]) {
    if (key === 'group' || (key === 'course' && courseId)) continue
    const value = params.get(key)
    if (value) next.set(key, value)
  }
  next.set('group', projectId)
  return `${courseId ? overviewPath(courseId) : legacyPath(source)}?${next}`
}

export function teacherObservationLink(
  projectId: string,
  source: ObservationSource,
  params: URLSearchParams,
  courseId?: string,
) {
  const query = new URLSearchParams({
    observe: source,
    returnTo: listDestination(source, params, projectId, courseId),
  })
  return `/project/${encodeURIComponent(projectId)}?${query}`
}

export function teacherObservationReturn(
  params: URLSearchParams,
  project?: Pick<Project, 'id' | 'course_id'> | null,
): { to: string; scoped: boolean } {
  const requestedSource = params.get('observe')
  const source = requestedSource === 'manager' ? 'manager' : 'monitor'
  const fallback = { to: legacyPath(source), scoped: false }
  const value = params.get('returnTo')
  if (!project || !value || (requestedSource !== 'manager' && requestedSource !== 'monitor')) return fallback

  const separator = value.indexOf('?')
  if (separator < 0) return fallback
  const path = value.slice(0, separator)
  const query = new URLSearchParams(value.slice(separator + 1))

  if (
    project.course_id && path === overviewPath(project.course_id)
    && query.get('section') === (source === 'manager' ? 'members' : 'observe')
    && (source !== 'manager' || !query.has('panel') || query.get('panel') === 'groups')
  ) {
    return { to: listDestination(source, query, project.id, project.course_id), scoped: true }
  }
  if (path !== legacyPath(source)) return fallback
  const courseFilter = query.get('course')
  const unassignedValue = source === 'manager' ? 'unbound' : '__unassigned__'
  if (
    courseFilter && courseFilter !== 'all' && courseFilter !== project.course_id
    && (project.course_id || courseFilter !== unassignedValue)
  ) return fallback

  return { to: listDestination(source, query, project.id), scoped: false }
}

export async function loadTeacherProjectList(courseId?: string, firstPageOnly = false): Promise<Project[]> {
  const byId = new Map<string, Project>()
  let skip = 0
  let hasMore: boolean
  do {
    const page = await projectService.getProjects(undefined, skip, courseId)
    for (const project of page.projects) {
      // Older backends may ignore the course filter during a mixed deployment.
      if (!courseId || project.course_id === courseId) byId.set(project.id, project)
    }
    skip += page.projects.length
    hasMore = !firstPageOnly && page.projects.length > 0 && skip < page.total
  } while (hasMore)
  return Array.from(byId.values())
}
