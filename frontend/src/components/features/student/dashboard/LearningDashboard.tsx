import { useEffect, useState } from 'react'
import { ArrowUpRight, FileText, RefreshCw } from 'lucide-react'
import {
    analyticsService,
    type LearningOverviewTarget,
    type StudentLearningOverview,
} from '../../../../services/api/analytics'

type Props = {
    projectId: string
    onOpen: (target: LearningOverviewTarget) => void
    canOpen?: (target: LearningOverviewTarget) => boolean
}

type Period = 7 | 30
type Result = { projectId: string; days: Period; data: StudentLearningOverview | null }
type Suggestion = StudentLearningOverview['suggestions']['group']

const periods = [7, 30] as const
const restriction = '当前阶段暂不可打开'
const actionClass = 'inline-flex min-h-9 items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:text-slate-500 disabled:hover:bg-white'

const dateTime = new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'short',
    timeStyle: 'short',
})

function Timestamp({ value }: { value: string }) {
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return null
    return <time dateTime={value}>{dateTime.format(date)}</time>
}

function ActivityCounts({ counts, empty }: {
    counts: [label: string, count: number][]
    empty: string
}) {
    if (counts.every(([, count]) => count === 0)) {
        return <p className="py-3 text-sm text-slate-500">{empty}</p>
    }

    return (
        <dl className="divide-y divide-slate-100">
            {counts.map(([label, count]) => (
                <div key={label} className="flex items-baseline justify-between gap-3 py-2">
                    <dt className="text-sm text-slate-600">{label}</dt>
                    <dd className="font-semibold tabular-nums text-slate-900">{count}</dd>
                </div>
            ))}
        </dl>
    )
}

function NextStep({ suggestion, onOpen, canOpen }: Pick<Props, 'onOpen' | 'canOpen'> & {
    suggestion: Suggestion
}) {
    if (!suggestion) return null
    const { basis, action, target } = suggestion
    const allowed = canOpen?.(target) ?? true

    return (
        <div className="mt-4 border-l-2 border-teal-600 pl-3">
            <h3 className="text-sm font-semibold text-slate-900">下一步建议</h3>
            <p className="mt-1 break-words text-sm leading-relaxed text-slate-600">依据：{basis}</p>
            <button
                type="button"
                className={`${actionClass} mt-2 max-w-full py-1.5 text-left`}
                onClick={() => onOpen(target)}
                disabled={!allowed}
            >
                <span className="min-w-0 break-words">{action}</span>
                <ArrowUpRight className="h-4 w-4 shrink-0" aria-hidden="true" />
            </button>
            {!allowed && <p className="mt-1 text-xs text-slate-500">{restriction}</p>}
        </div>
    )
}

export default function LearningDashboard({ projectId, onOpen, canOpen }: Props) {
    const [days, setDays] = useState<Period>(7)
    const [result, setResult] = useState<Result | null>(null)
    const [refreshCount, setRefreshCount] = useState(0)

    const reload = () => {
        setResult(null)
        setRefreshCount((count) => count + 1)
    }

    useEffect(() => {
        const controller = new AbortController()
        setResult(null)

        async function load() {
            try {
                const data = await analyticsService.getStudentProcessDashboard(projectId, days, controller.signal)
                if (!controller.signal.aborted) setResult({ projectId, days, data })
            } catch {
                if (!controller.signal.aborted) setResult({ projectId, days, data: null })
            }
        }

        void load()
        return () => controller.abort()
    }, [projectId, days, refreshCount])

    // A filter or project change renders before the old effect is cleaned up.
    const current = result?.projectId === projectId && result.days === days ? result : null
    const overview = current?.data

    return (
        <div className="h-full overflow-y-auto bg-white text-slate-900">
            <div className="mx-auto max-w-6xl px-4 py-4 sm:px-6">
                <header className="mb-5 flex flex-wrap items-center justify-between gap-x-5 gap-y-3">
                    <h1 className="text-lg font-semibold">学习概览</h1>
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                        <div className="flex items-center gap-2">
                            {overview && (
                                <p className="text-xs text-slate-500">更新于 <Timestamp value={overview.updatedAt} /></p>
                            )}
                            <button type="button" aria-label="刷新学习记录" title="刷新学习记录"
                                onClick={reload} disabled={!overview}
                                className="grid h-9 w-9 place-items-center rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-40">
                                <RefreshCw className="h-4 w-4" aria-hidden="true" />
                            </button>
                        </div>
                        <div role="group" aria-label="统计时段" className="inline-flex rounded-md border border-slate-200 bg-slate-50 p-0.5">
                            {periods.map((period) => (
                                <button
                                    key={period}
                                    type="button"
                                    aria-pressed={days === period}
                                    onClick={() => setDays(period)}
                                    className={`min-h-9 whitespace-nowrap rounded px-2.5 text-sm font-medium ${days === period
                                        ? 'bg-white text-teal-800 shadow-sm'
                                        : 'text-slate-600 hover:bg-slate-100'
                                    }`}
                                >
                                    最近{period}天
                                </button>
                            ))}
                        </div>
                    </div>
                </header>

                {!current && <p role="status" className="border-t border-slate-200 py-3 text-sm text-slate-600">正在加载学习记录…</p>}
                {current && !overview && (
                    <div className="border-t border-slate-200 py-3">
                        <p role="alert" className="text-sm text-slate-700">暂时无法加载学习记录。</p>
                        <button
                            type="button"
                            className={`${actionClass} mt-2`}
                            onClick={reload}
                        >
                            <RefreshCw className="h-4 w-4" aria-hidden="true" />
                            重试
                        </button>
                    </div>
                )}
                {overview && (
                    <div className="flex flex-wrap gap-x-8 gap-y-6">
                        {overview.personal && (
                            <section aria-label="我的学习" className="min-w-0 flex-1 basis-80 border-t border-slate-200 pt-3">
                                <h2 className="mb-1 text-base font-semibold">我的学习</h2>
                                <ActivityCounts
                                    empty="本时段暂无个人学习记录。"
                                    counts={[
                                        ['发送的小组消息', overview.personal.messagesSent],
                                        ['其他同学 @ 我的消息', overview.personal.mentionsReceived],
                                        ['手动保存的共享文档版本', overview.personal.sharedDocumentSaves],
                                        ['新增的知识条目', overview.personal.knowledgeEntriesAdded],
                                    ]}
                                />
                                <NextStep suggestion={overview.suggestions.personal} onOpen={onOpen} canOpen={canOpen} />
                            </section>
                        )}
                        <section aria-label="小组学习" className="min-w-0 flex-1 basis-80 border-t border-slate-200 pt-3">
                            <h2 className="mb-1 text-base font-semibold">小组学习</h2>
                            <ActivityCounts
                                empty="本时段暂无小组学习记录。"
                                counts={[
                                    ['小组消息', overview.group.messagesSent],
                                    ['在组发言学生', overview.group.activeDiscussants],
                                    ['新建或更新的共享文档', overview.group.sharedDocumentsUpdated],
                                    ['证据条目', overview.group.evidenceEntries],
                                    ['待核验的分歧条目', overview.group.openIssues],
                                ]}
                            />
                            <NextStep suggestion={overview.suggestions.group} onOpen={onOpen} canOpen={canOpen} />
                            <div className="mt-4 border-t border-slate-200 pt-3">
                                <h3 className="text-sm font-semibold">最近文档</h3>
                                {overview.group.recentDocuments.length === 0 ? (
                                    <p className="mt-2 text-sm text-slate-500">本时段暂无文档记录。</p>
                                ) : (
                                    <ul className="mt-1 divide-y divide-slate-100">
                                        {overview.group.recentDocuments.map((document) => {
                                            const target: LearningOverviewTarget = { kind: 'document', documentId: document.id }
                                            const allowed = canOpen?.(target) ?? true
                                            return (
                                                <li key={document.id}>
                                                    <button
                                                        type="button"
                                                        onClick={() => onOpen(target)}
                                                        disabled={!allowed}
                                                        className="flex min-h-11 w-full items-center gap-2 rounded-md px-1 py-2 text-left text-sm hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-500 disabled:hover:bg-transparent"
                                                    >
                                                        <FileText className="h-4 w-4 shrink-0 text-slate-500" aria-hidden="true" />
                                                        <span className="flex min-w-0 flex-1 flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
                                                            <span className="min-w-0 break-words font-medium">{document.title}</span>
                                                            <span className="text-xs text-slate-500">
                                                                <Timestamp value={document.updatedAt} />
                                                                {!allowed && <span className="ml-2">{restriction}</span>}
                                                            </span>
                                                        </span>
                                                        {allowed && <ArrowUpRight className="h-4 w-4 shrink-0 text-slate-500" aria-hidden="true" />}
                                                    </button>
                                                </li>
                                            )
                                        })}
                                    </ul>
                                )}
                            </div>
                        </section>
                    </div>
                )}
            </div>
        </div>
    )
}
