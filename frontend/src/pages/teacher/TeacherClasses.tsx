import { lazy, Suspense, useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, BookOpen, ChevronLeft, ChevronRight, ClipboardList, FolderDown, LayoutGrid, List, Monitor, RefreshCw, Settings, Users } from 'lucide-react'
import { ROUTES } from '../../config/routes'
import { courseService, type Course, type Student } from '../../services/api/course'
import { useAuthStore } from '../../stores/authStore'
import ClassAISettings from './ClassAISettings'
import CourseExports from './CourseExports'
import CourseLearningDesign from './CourseLearningDesign'

const ProjectManager = lazy(() => import('../../components/features/teacher/projectmanager/ProjectManager'))
const ProjectMonitor = lazy(() => import('../../components/features/teacher/projectmonitor/ProjectMonitor'))
const CourseTaskRelease = lazy(() => import('../../components/features/teacher/taskrelease/CourseTaskRelease'))
const CourseResource = lazy(() => import('../../components/features/teacher/courseresource/CourseResource'))
const AssignmentReview = lazy(() => import('../../components/features/teacher/assignmentreview/AssignmentReview'))
const ClassManagement = lazy(() => import('../../components/features/teacher/classmanagement/ClassManagement'))

type Load<T> = { data: T } | { error: string } | null
const home = ROUTES.TEACHER.OVERVIEW
const selectStyle = 'max-w-full min-w-0 rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm'
const classSections = [
  { id: 'overview', label: '班级概览', icon: BookOpen },
  { id: 'members', label: '成员与小组', icon: Users },
  { id: 'activity', label: '活动设计', icon: ClipboardList },
  { id: 'observe', label: '小组观察', icon: Monitor },
  { id: 'outcomes', label: '成果与导出', icon: FolderDown },
  { id: 'settings', label: '班级设置', icon: Settings },
] as const
type ClassSection = typeof classSections[number]['id']
const classPanels = {
  members: [{ id: 'roster', label: '学生名单' }, { id: 'groups', label: '小组管理' }],
  activity: [{ id: 'design', label: 'AI 与阶段' }, { id: 'tasks', label: '任务发布' }, { id: 'resources', label: '课程资料' }],
  outcomes: [{ id: 'exports', label: '数据导出' }, { id: 'reviews', label: '成果评审' }],
} as const
const homeFilters = ['year', 'term', 'view', 'density']

function getHomeParams(params: URLSearchParams) {
  const next = new URLSearchParams()
  for (const key of homeFilters) {
    const value = params.get(key)
    if (value !== null) next.set(key, value)
  }
  return next
}

function academicYear(semester: string) {
  const range = semester.match(/^\s*(\d{4})\s*[-–—/]\s*(\d{4})(?!\d)/)
  if (range && Number(range[2]) === Number(range[1]) + 1) return `${range[1]}–${range[2]}`
  const term = semester.match(/^\s*(\d{4})\s*年?\s*(春|秋)/)
  if (!term) return '未归入学年'
  const start = Number(term[1]) - (term[2] === '春' ? 1 : 0)
  return `${start}–${start + 1}`
}

export default function TeacherClasses() {
  const { courseId } = useParams()
  const [params, setParams] = useSearchParams()
  const userId = useAuthStore(state => state.user?.id)

  return <OwnedClasses key={userId} userId={userId} courseId={courseId} params={params}
    change={(key, value) => {
      const next = new URLSearchParams(params)
      if (value !== null && (value || key === 'term')) next.set(key, value)
      else next.delete(key)
      if (key === 'year') next.delete('term')
      setParams(next, { replace: true })
    }} />
}

function OwnedClasses({ userId, courseId, params, change }: {
  userId?: string
  courseId?: string
  params: URLSearchParams
  change: (key: string, value: string | null) => void
}) {
  const [result, setResult] = useState<Load<Course[]>>(null)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let active = true
    courseService.getCourses().then(
      courses => { if (active) setResult({ data: courses.filter(course => course.teacher_id === userId) }) },
      () => { if (active) setResult({ error: '无法加载班级' }) }
    )
    return () => { active = false }
  }, [attempt, userId])

  const search = getHomeParams(params).toString()
  if (courseId) {
    const course = result && 'data' in result ? result.data.find(item => item.id === courseId) : undefined
    return (
      <section className="space-y-5">
        <Link to={{ pathname: home, search }} className="inline-flex items-center gap-2 text-sm font-medium text-indigo-700 hover:underline">
          <ArrowLeft size={16} /> 返回我的班级
        </Link>
        {course ? <ClassDetail key={course.id} course={course} params={params}
          onUpdated={updated => setResult(previous => previous && 'data' in previous
            ? { data: previous.data.map(item => item.id === updated.id ? updated : item) }
            : previous)} /> : (
          <LoadMessage result={result} empty="找不到该班级，或该班级不属于当前账号。"
            retry={() => { setResult(null); setAttempt(attempt + 1) }} />
        )}
      </section>
    )
  }

  const courses = result && 'data' in result ? result.data : []
  const years = [...new Set(courses.map(course => academicYear(course.semester)))].sort().reverse()
  const requestedYear = params.get('year') ?? ''
  const year = years.includes(requestedYear) ? requestedYear : ''
  const yearCourses = courses.filter(course => !year || academicYear(course.semester) === year)
  const terms = [...new Set(yearCourses.map(course => course.semester || ''))].sort().reverse()
  const term = params.get('term')
  const rows = yearCourses.filter(course => term === null || !terms.includes(term) || (course.semester || '') === term)
  const list = params.get('view') === 'list'
  const compact = params.get('density') === 'compact'

  return (
    <section className="space-y-5">
      <header>
        <h1 className="text-2xl font-semibold">我的班级</h1>
        {result && 'data' in result && <p className="mt-1 text-sm text-slate-600" role="status">显示 {rows.length} 个班级，共 {courses.length} 个</p>}
      </header>
      {courses.length > 0 && (
        <div className="flex flex-wrap items-end gap-x-4 gap-y-3 border-y border-slate-200 py-3">
          <label className="grid min-w-0 gap-1 text-xs font-medium">学年
            <select className={selectStyle} value={year} onChange={event => change('year', event.target.value)}>
              <option value="">全部学年</option>
              {years.map(value => <option key={value}>{value}</option>)}
            </select>
          </label>
          <label className="grid min-w-0 max-w-full gap-1 text-xs font-medium">学期
            <select className={selectStyle} value={term !== null && terms.includes(term) ? terms.indexOf(term) : -1}
              onChange={event => change('term', terms[Number(event.target.value)] ?? null)}>
              <option value={-1}>全部学期</option>
              {terms.map((value, index) => <option key={value} value={index}>{value || '未设置学期'}</option>)}
            </select>
          </label>
          <div role="group" aria-label="班级视图" className="flex rounded-md border border-slate-300 bg-white p-0.5 sm:ml-auto">
            {([{ value: '', icon: LayoutGrid, label: '卡片', active: !list }, { value: 'list', icon: List, label: '列表', active: list }]).map(({ value, icon: Icon, label, active }) => (
              <button key={label} type="button" title={label} aria-label={label} aria-pressed={active}
                onClick={() => change('view', value)}
                className={`grid h-8 w-9 place-items-center rounded ${active ? 'bg-indigo-100 text-indigo-800' : 'text-slate-600 hover:bg-slate-100'}`}>
                <Icon size={17} />
              </button>
            ))}
          </div>
          <label className="grid gap-1 text-xs font-medium">密度
            <select className={selectStyle} value={compact ? 'compact' : ''} onChange={event => change('density', event.target.value)}>
              <option value="">舒展</option>
              <option value="compact">紧凑</option>
            </select>
          </label>
        </div>
      )}
      {rows.length ? (
        <ul className={list ? `grid ${compact ? 'gap-1.5' : 'gap-3'}` : `grid sm:grid-cols-2 xl:grid-cols-3 ${compact ? 'gap-2' : 'gap-4'}`}>
          {rows.map(course => (
            <li key={course.id} className="min-w-0">
              <Link to={{ pathname: `${home}/${encodeURIComponent(course.id)}`, search }}
                className={`group flex h-full min-w-0 items-start gap-3 rounded-lg border border-slate-200 bg-white hover:border-indigo-400 focus-visible:outline-indigo-600 ${compact ? 'p-3' : 'p-5'}`}>
                <BookOpen className="mt-0.5 shrink-0 text-indigo-700" size={18} aria-hidden="true" />
                <div className={`min-w-0 flex-1 ${list ? 'flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1' : 'space-y-2'}`}>
                  <h2 className="break-words font-semibold group-hover:text-indigo-700">{course.name}</h2>
                  <p className="break-words text-sm text-slate-600">{course.semester || '未设置学期'} · {course.students.length} 名在册学生</p>
                </div>
                <ChevronRight className="mt-0.5 shrink-0 text-slate-500" size={17} aria-hidden="true" />
              </Link>
            </li>
          ))}
        </ul>
      ) : <LoadMessage result={result} empty={courses.length ? '没有符合筛选条件的班级。' : '当前账号暂无班级。'}
        retry={() => { setResult(null); setAttempt(attempt + 1) }} />}
    </section>
  )
}

function ClassDetail({ course, params, onUpdated }: {
  course: Course
  params: URLSearchParams
  onUpdated: (course: Course) => void
}) {
  const active = classSections.find(section => section.id === params.get('section'))?.id ?? 'overview'
  const panels = active in classPanels ? classPanels[active as keyof typeof classPanels] : undefined
  const panel = panels?.find(item => item.id === params.get('panel'))?.id ?? panels?.[0].id
  const pathname = `${home}/${encodeURIComponent(course.id)}`
  const location = (section: ClassSection, subview?: string) => {
    const next = getHomeParams(params)
    if (section !== 'overview') next.set('section', section)
    if (subview) next.set('panel', subview)
    return { pathname, search: next.toString() }
  }
  const classNavLink = (current: boolean) => `inline-flex min-h-9 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1.5 font-medium focus-visible:outline-indigo-600 ${current ? 'bg-indigo-100 text-indigo-900' : 'text-slate-700 hover:bg-slate-100'}`
  const navStyle = 'flex min-w-0 flex-wrap gap-1 text-sm'

  return (
    <>
      <header className="space-y-1">
        <h1 className="break-words text-2xl font-semibold">{course.name}</h1>
        <p className="break-words text-sm text-slate-600">{course.semester || '未设置学期'} · {course.students.length} 名在册学生</p>
      </header>
      <nav aria-label="班级工作台" className={`${navStyle} border-y border-slate-200 py-2`}>
        {classSections.map(({ id, label, icon: Icon }) => (
          <Link key={id} to={location(id)} aria-current={active === id ? 'page' : undefined}
            className={classNavLink(active === id)}>
            <Icon size={16} className="shrink-0" aria-hidden="true" /> {label}
          </Link>
        ))}
      </nav>
      {panels && (
        <nav aria-label="班级分区" className={navStyle}>
          {panels.map(item => (
            <Link key={item.id} to={location(active, item.id)} aria-current={panel === item.id ? 'page' : undefined}
              className={classNavLink(panel === item.id)}>{item.label}</Link>
          ))}
        </nav>
      )}
      <Suspense fallback={<p role="status" className="py-3 text-sm text-slate-600">正在加载班级分区…</p>}>
        {active === 'overview' && (
          <section aria-label="班级概览" className="space-y-5">
            {course.description && <p className="whitespace-pre-wrap break-words text-sm text-slate-700">{course.description}</p>}
            <dl className="grid gap-4 border-b border-slate-200 pb-5 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-slate-600">在册学生</dt>
                <dd className="mt-1"><Link to={location('members')} className="font-medium text-indigo-700 hover:underline">{course.students.length} 人 · 查看名单</Link></dd>
              </div>
              <div>
                <dt className="text-slate-600">班级邀请码</dt>
                <dd className="mt-1 break-all font-mono font-medium">{course.invite_code || '未设置'}</dd>
              </div>
            </dl>
          </section>
        )}
        {active === 'members' && (panel === 'groups' ? <ProjectManager course={course} /> : <ClassRoster courseId={course.id} />)}
        {active === 'activity' && (
          panel === 'tasks' ? <CourseTaskRelease course={course} /> :
            panel === 'resources' ? <CourseResource course={course} /> :
              <div className="min-w-0 space-y-5">
                <ClassAISettings courseId={course.id} />
                <CourseLearningDesign courseId={course.id} />
              </div>
        )}
        {active === 'observe' && <ProjectMonitor course={course} />}
        {active === 'outcomes' && (panel === 'reviews' ? <AssignmentReview courseId={course.id} /> : <CourseExports courseId={course.id} embedded />)}
        {active === 'settings' && <ClassManagement course={course} onCourseUpdated={onUpdated} />}
      </Suspense>
    </>
  )
}

function ClassRoster({ courseId }: { courseId: string }) {
  const [result, setResult] = useState<Load<Student[]>>(null)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let active = true
    courseService.getCourseStudents(courseId).then(
      students => { if (active) setResult({ data: students }) },
      () => { if (active) setResult({ error: '无法加载学生名单' }) }
    )
    return () => { active = false }
  }, [courseId, attempt])

  const students = result && 'data' in result ? result.data : []
  return (
    <section aria-labelledby="class-roster">
      <header className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 id="class-roster" className="flex items-center gap-2 font-semibold"><Users size={18} /> 学生名单</h2>
        <Link to={`${ROUTES.TEACHER.STUDENTS}?courseId=${encodeURIComponent(courseId)}`}
          className="rounded-md border border-slate-300 px-2.5 py-1.5 text-sm font-medium hover:bg-slate-100">
          导入与管理学生
        </Link>
      </header>
      {students.length ? (
        <StudentRoster students={students} />
      ) : <LoadMessage result={result} empty="暂无学生名单记录。"
        retry={() => { setResult(null); setAttempt(attempt + 1) }} />}
    </section>
  )
}

function StudentRoster({ students }: { students: Student[] }) {
  const [pageSize, setPageSize] = useState(10)
  const [requestedPage, setRequestedPage] = useState(1)
  const [draft, setDraft] = useState<string | null>(null)
  const pageCount = Math.ceil(students.length / pageSize)
  const page = Math.min(requestedPage, pageCount)
  const goToPage = (value: number) => {
    setRequestedPage(Math.max(1, Math.min(value, pageCount)))
    setDraft(null)
  }
  const pageButton = 'grid h-8 w-8 place-items-center rounded-md border border-slate-300 bg-white hover:bg-slate-100 disabled:opacity-40 disabled:hover:bg-white'

  return (
    <>
      <ul className="divide-y divide-slate-200 border-y border-slate-200">
        {students.slice((page - 1) * pageSize, page * pageSize).map(student => (
          <li key={student.id} className="grid gap-1 py-3 text-sm sm:grid-cols-2">
            <span className="break-words font-medium">{student.username}</span>
            <span className="break-words text-slate-600">{student.email}</span>
          </li>
        ))}
      </ul>
      <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
        <label className="flex items-center gap-2">每页条数
          <select className={selectStyle} value={pageSize} onChange={event => {
            setPageSize(Number(event.target.value))
            goToPage(1)
          }}>
            {[10, 25, 50, 100].map(size => <option key={size}>{size}</option>)}
          </select>
        </label>
        <p role="status" className="text-slate-600">第 {page} / {pageCount} 页，共 {students.length} 条</p>
        <div className="flex items-center gap-1">
          <button type="button" className={pageButton} title="上一页" aria-label="上一页" disabled={page === 1} onClick={() => goToPage(page - 1)}>
            <ChevronLeft size={16} />
          </button>
          <button type="button" className={pageButton} title="下一页" aria-label="下一页" disabled={page === pageCount} onClick={() => goToPage(page + 1)}>
            <ChevronRight size={16} />
          </button>
        </div>
        <form className="flex items-center gap-2" onSubmit={event => {
          event.preventDefault()
          const value = (draft ?? String(page)).trim()
          if (/^-?[0-9]+$/.test(value)) goToPage(Number(value))
          else setDraft(null)
        }}>
          <input aria-label="跳转页码" inputMode="numeric" className={`${selectStyle} w-16`}
            value={draft ?? String(page)} onChange={event => setDraft(event.target.value)} />
          <button type="submit" className={selectStyle}>跳转</button>
        </form>
      </div>
    </>
  )
}

function LoadMessage({ result, empty, retry }: { result: Load<unknown>; empty: string; retry: () => void }) {
  return (
    <div className="border-y border-slate-200 py-5 text-sm text-slate-600">
      <p role="status">{!result ? '正在加载…' : 'error' in result ? result.error : empty}</p>
      {result && 'error' in result && (
        <button type="button" onClick={retry} className="mt-2 inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-1.5 hover:bg-slate-100">
          <RefreshCw size={14} /> 重试
        </button>
      )}
    </div>
  )
}
