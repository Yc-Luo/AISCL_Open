// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { LearningOverviewTarget, StudentLearningOverview } from '../../../../services/api/analytics'
import LearningDashboard from './LearningDashboard'

const { getStudentProcessDashboard } = vi.hoisted(() => ({ getStudentProcessDashboard: vi.fn() }))

vi.mock('../../../../services/api/analytics', () => ({
    analyticsService: { getStudentProcessDashboard },
}))

const empty: StudentLearningOverview = {
    updatedAt: '2026-11-24T10:00:00Z',
    periodDays: 7,
    personal: {
        messagesSent: 0,
        mentionsReceived: 0,
        sharedDocumentSaves: 0,
        knowledgeEntriesAdded: 0,
    },
    group: {
        messagesSent: 0,
        activeDiscussants: 0,
        sharedDocumentsUpdated: 0,
        evidenceEntries: 0,
        openIssues: 0,
        recentDocuments: [],
    },
    suggestions: { personal: null, group: null },
}

const populated: StudentLearningOverview = {
    ...empty,
    personal: {
        messagesSent: 4,
        mentionsReceived: 2,
        sharedDocumentSaves: 3,
        knowledgeEntriesAdded: 1,
    },
    group: {
        messagesSent: 12,
        activeDiscussants: 5,
        sharedDocumentsUpdated: 2,
        evidenceEntries: 6,
        openIssues: 7,
        recentDocuments: [{ id: 'doc-1', title: '证据汇总', updatedAt: empty.updatedAt }],
    },
    suggestions: {
        personal: {
            basis: '有同学在讨论中提到你。',
            action: '查看小组讨论',
            target: { kind: 'chat' },
        },
        group: {
            basis: '有待核验的分歧条目。',
            action: '查看知识条目',
            target: { kind: 'wiki' },
        },
    },
}

function pendingOverview() {
    let resolve!: (data: StudentLearningOverview) => void
    let reject!: (reason: Error) => void
    const promise = new Promise<StudentLearningOverview>((done, fail) => { resolve = done; reject = fail })
    return { promise, resolve, reject }
}

beforeEach(() => getStudentProcessDashboard.mockReset())
afterEach(cleanup)

describe('LearningDashboard', () => {
    it('shows the two sets of counts and opens the exact suggestion and document targets', async () => {
        getStudentProcessDashboard.mockResolvedValueOnce(populated)
        const onOpen = vi.fn()
        render(<LearningDashboard projectId="group-a" onOpen={onOpen} />)

        const personal = within(await screen.findByRole('region', { name: '我的学习' }))
        const group = within(screen.getByRole('region', { name: '小组学习' }))
        expect(getStudentProcessDashboard).toHaveBeenCalledWith('group-a', 7, expect.any(AbortSignal))
        expect(personal.getByText('其他同学 @ 我的消息').nextElementSibling?.textContent).toBe('2')
        expect(personal.getByText('手动保存的共享文档版本').nextElementSibling?.textContent).toBe('3')
        expect(group.getByText('在组发言学生').nextElementSibling?.textContent).toBe('5')
        expect(group.getByText('新建或更新的共享文档').nextElementSibling?.textContent).toBe('2')
        expect(group.getByText('待核验的分歧条目').nextElementSibling?.textContent).toBe('7')
        expect(personal.getByText('依据：有同学在讨论中提到你。')).toBeTruthy()
        expect(group.getByText('依据：有待核验的分歧条目。')).toBeTruthy()

        fireEvent.click(personal.getByRole('button', { name: '查看小组讨论' }))
        fireEvent.click(group.getByRole('button', { name: '查看知识条目' }))
        fireEvent.click(group.getByRole('button', { name: /^证据汇总/ }))
        expect(onOpen.mock.calls).toEqual([
            [{ kind: 'chat' }],
            [{ kind: 'wiki' }],
            [{ kind: 'document', documentId: 'doc-1' }],
        ])
    })

    it('disables restricted suggestions and documents and reflects updated workspace permissions', async () => {
        getStudentProcessDashboard.mockResolvedValueOnce({
            ...populated,
            suggestions: {
                personal: populated.suggestions.personal,
                group: { basis: '建议整理证据。', action: '进入共享文档', target: { kind: 'document' } },
            },
        })
        const onOpen = vi.fn()
        const canOpen = (target: LearningOverviewTarget) => target.kind !== 'document'
        const { rerender } = render(<LearningDashboard projectId="group-a" onOpen={onOpen} canOpen={canOpen} />)

        const suggestion = await screen.findByRole<HTMLButtonElement>('button', { name: '进入共享文档' })
        const document = screen.getByRole<HTMLButtonElement>('button', { name: /^证据汇总/ })
        expect(suggestion.disabled).toBe(true)
        expect(document.disabled).toBe(true)
        expect(screen.getAllByText('当前阶段暂不可打开')).toHaveLength(2)
        fireEvent.click(suggestion)
        fireEvent.click(document)
        expect(onOpen).not.toHaveBeenCalled()

        rerender(<LearningDashboard projectId="group-a" onOpen={onOpen} canOpen={() => true} />)
        expect(suggestion.disabled).toBe(false)
        expect(document.disabled).toBe(false)
        expect(screen.queryByText('当前阶段暂不可打开')).toBeNull()
        fireEvent.click(suggestion)
        expect(onOpen).toHaveBeenCalledWith({ kind: 'document' })
        expect(getStudentProcessDashboard).toHaveBeenCalledTimes(1)
    })

    it('shows group-only data and no suggestions when the response omits them', async () => {
        getStudentProcessDashboard.mockResolvedValueOnce({ ...populated, personal: null, suggestions: empty.suggestions })
        render(<LearningDashboard projectId="group-a" onOpen={vi.fn()} />)

        expect(await screen.findByRole('heading', { name: '小组学习' })).toBeTruthy()
        expect(screen.queryByRole('heading', { name: '我的学习' })).toBeNull()
        expect(screen.queryByRole('heading', { name: '下一步建议' })).toBeNull()
        expect(screen.getByRole('button', { name: /^证据汇总/ })).toBeTruthy()
    })

    it('distinguishes empty personal, group, and document records', async () => {
        getStudentProcessDashboard.mockResolvedValueOnce(empty)
        render(<LearningDashboard projectId="group-a" onOpen={vi.fn()} />)

        expect(await screen.findByText('本时段暂无个人学习记录。')).toBeTruthy()
        expect(screen.getByText('本时段暂无小组学习记录。')).toBeTruthy()
        expect(screen.getByText('本时段暂无文档记录。')).toBeTruthy()
    })

    it('uses one period for both views and ignores a late response for the previous period', async () => {
        const previous = pendingOverview()
        getStudentProcessDashboard.mockReturnValueOnce(previous.promise)
            .mockResolvedValueOnce({ ...empty, periodDays: 30 })
        render(<LearningDashboard projectId="group-a" onOpen={vi.fn()} />)
        const oldSignal: AbortSignal = getStudentProcessDashboard.mock.calls[0][2]

        fireEvent.click(screen.getByRole('button', { name: '最近30天' }))
        expect(oldSignal.aborted).toBe(true)
        expect(getStudentProcessDashboard).toHaveBeenLastCalledWith('group-a', 30, expect.any(AbortSignal))
        expect(screen.getByRole('button', { name: '最近30天' }).getAttribute('aria-pressed')).toBe('true')
        expect(await screen.findByText('本时段暂无个人学习记录。')).toBeTruthy()
        await act(async () => previous.resolve(populated))
        expect(screen.getByText('本时段暂无小组学习记录。')).toBeTruthy()
        expect(screen.queryByRole('button', { name: /^证据汇总/ })).toBeNull()
    })

    it('clears old group records immediately and ignores an aborted group request failure', async () => {
        const previous = pendingOverview()
        const next = pendingOverview()
        getStudentProcessDashboard.mockResolvedValueOnce(populated)
            .mockReturnValueOnce(previous.promise)
            .mockReturnValueOnce(next.promise)
        const onOpen = vi.fn()
        const { rerender, unmount } = render(<LearningDashboard projectId="group-a" onOpen={onOpen} />)
        expect(await screen.findByRole('button', { name: /^证据汇总/ })).toBeTruthy()

        fireEvent.click(screen.getByRole('button', { name: '最近30天' }))
        expect(screen.queryByRole('button', { name: /^证据汇总/ })).toBeNull()
        const oldSignal: AbortSignal = getStudentProcessDashboard.mock.calls[1][2]
        rerender(<LearningDashboard projectId="group-b" onOpen={onOpen} />)
        expect(oldSignal.aborted).toBe(true)
        expect(getStudentProcessDashboard).toHaveBeenLastCalledWith('group-b', 30, expect.any(AbortSignal))
        await act(async () => previous.reject(new Error('Old group failed')))
        expect(screen.queryByRole('alert')).toBeNull()
        expect(screen.getByRole('status').textContent).toContain('正在加载学习记录')

        const newSignal: AbortSignal = getStudentProcessDashboard.mock.calls[2][2]
        unmount()
        expect(newSignal.aborted).toBe(true)
        await act(async () => next.resolve({ ...empty, periodDays: 30 }))
    })

    it('retries a failed request with the current period', async () => {
        getStudentProcessDashboard.mockResolvedValueOnce(empty)
            .mockRejectedValueOnce(new Error('Unavailable'))
            .mockResolvedValueOnce({ ...empty, periodDays: 30 })
        render(<LearningDashboard projectId="group-a" onOpen={vi.fn()} />)
        await screen.findByRole('heading', { name: '小组学习' })

        fireEvent.click(screen.getByRole('button', { name: '最近30天' }))
        expect((await screen.findByRole('alert')).textContent).toBe('暂时无法加载学习记录。')
        fireEvent.click(screen.getByRole('button', { name: '重试' }))
        expect(getStudentProcessDashboard).toHaveBeenLastCalledWith('group-a', 30, expect.any(AbortSignal))
        expect(await screen.findByText('本时段暂无小组学习记录。')).toBeTruthy()
        expect(screen.queryByRole('alert')).toBeNull()
    })

    it('refreshes saved records without changing the period', async () => {
        getStudentProcessDashboard.mockResolvedValueOnce(empty).mockResolvedValueOnce(populated)
        render(<LearningDashboard projectId="group-a" onOpen={vi.fn()} />)
        await screen.findByText('本时段暂无小组学习记录。')

        fireEvent.click(screen.getByRole('button', { name: '刷新学习记录' }))

        expect(await screen.findByRole('button', { name: /^证据汇总/ })).toBeTruthy()
        expect(getStudentProcessDashboard).toHaveBeenCalledTimes(2)
        expect(getStudentProcessDashboard).toHaveBeenLastCalledWith('group-a', 7, expect.any(AbortSignal))
    })
})
