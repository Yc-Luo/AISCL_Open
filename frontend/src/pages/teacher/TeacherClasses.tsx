import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, BookOpen, ChevronLeft, ChevronRight, LayoutGrid, List, RefreshCw, Users } from 'lucide-react'
import { ROUTES } from '../../config/routes'
import { courseService, type Course, type Student } from '../../services/api/course'
import { useAuthStore } from '../../stores/authStore'
import ClassAISettings from './ClassAISettings'
import CourseLearningDesign from './CourseLearningDesign'

type Load<T> = { data: T } | { error: string } | null
const home = ROUTES.TEACHER.OVERVIEW
const selectStyle = 'max-w-full min-w-0 rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm'

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

  const search = params.toString()
  if (courseId) {
    const course = result && 'data' in result ? result.data.find(item => item.id === courseId) : undefined
    return (
      <section className="space-y-5">
        <Link to={{ pathname: home, search }} className="inline-flex items-center gap-2 text-sm font-medium text-indigo-700 hover:underline">
          <ArrowLeft size={16} /> 返回我的班级
        </Link>
        {course ? <ClassDetail key={course.id} course={course} /> : (
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

function ClassDetail({ course }: { course: Course }) {
  const [result, setResult] = useState<Load<Student[]>>(null)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let active = true
    courseService.getCourseStudents(course.id).then(
      students => { if (active) setResult({ data: students }) },
      () => { if (active) setResult({ error: '无法加载学生名单' }) }
    )
    return () => { active = false }
  }, [course.id, attempt])

  const students = result && 'data' in result ? result.data : []
  return (
    <>
      <header className="space-y-2 border-b border-slate-200 pb-5">
        <p className="text-sm font-medium text-indigo-700">班级详情</p>
        <h1 className="break-words text-2xl font-semibold">{course.name}</h1>
        <p className="break-words text-sm text-slate-600">{course.semester || '未设置学期'} · {course.students.length} 名在册学生</p>
        {course.description && <p className="whitespace-pre-wrap break-words text-sm text-slate-700">{course.description}</p>}
        <Link to={`${ROUTES.TEACHER.PROJECT_MANAGER}?course=${encodeURIComponent(course.id)}`}
          className="inline-flex items-center gap-2 rounded-md border border-slate-200 px-3 py-1.5 text-sm font-medium hover:bg-slate-50">
          <Users size={16} /> 管理本班小组
        </Link>
      </header>
      <ClassAISettings courseId={course.id} />
      <CourseLearningDesign courseId={course.id} />
      <section aria-labelledby="class-roster">
        <h2 id="class-roster" className="mb-3 flex items-center gap-2 font-semibold"><Users size={18} /> 学生名单</h2>
        {students.length ? (
          <StudentRoster students={students} />
        ) : <LoadMessage result={result} empty="暂无学生名单记录。"
          retry={() => { setResult(null); setAttempt(attempt + 1) }} />}
      </section>
    </>
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
