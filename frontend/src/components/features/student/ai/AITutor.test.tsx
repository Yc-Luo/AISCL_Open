// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { aiService } from '../../../../services/api/ai'
import { useContextStore } from '../../../../stores/contextStore'
import AITutor from './AITutor'

vi.mock('../../../../services/api/ai', () => ({
    aiService: { streamChat: vi.fn(), getConversations: vi.fn(), getMessages: vi.fn(), uploadAttachment: vi.fn() },
}))
vi.mock('../../../../services/tracking/TrackingService', () => ({ trackingService: { trackResearchEvent: vi.fn() } }))
vi.mock('../../../../modules/inquiry/hooks/useScrapbookActions', () => ({ useScrapbookActions: () => ({ addMaterial: vi.fn() }) }))

const projectId = 'group-a'
function open(isOpen = true) {
    return <AITutor projectId={projectId} projectName="比较证据小组" isOpen={isOpen} onClose={vi.fn()} />
}

function submit(content: string) {
    fireEvent.change(screen.getByRole('textbox', { name: '向 AI 导师提问' }), { target: { value: content } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
}

beforeEach(() => {
    vi.resetAllMocks()
    useContextStore.setState({ projectId, activeTab: 'document', documentId: 'doc-a', currentStage: 'meaning_exploration' })
})
afterEach(() => { cleanup(); useContextStore.setState(useContextStore.getInitialState()) })

it('keeps one conversation while sending fresh context for each tool', async () => {
    vi.mocked(aiService.streamChat).mockImplementation(async (_, handlers) => {
        handlers?.onMeta?.({ conversation_id: 'thread-a' })
        handlers?.onDone?.({ conversation_id: 'thread-a', final_content: '先比较来源' })
        return '先比较来源'
    })
    const view = render(open())
    expect(screen.getByText('比较证据小组 · 协作文档')).toBeTruthy()
    submit('我正在整理什么？')
    await screen.findByText('先比较来源')
    expect(vi.mocked(aiService.streamChat).mock.calls[0][0]).toMatchObject({
        project_id: projectId, active_tab: 'document', selected_document_id: 'doc-a',
        current_stage: 'meaning_exploration', context_source: 'floating_ai',
    })
    expect(vi.mocked(aiService.streamChat).mock.calls[0][0].conversation_id).toBeUndefined()
    expect(aiService.getConversations).not.toHaveBeenCalled()
    view.rerender(open(false))
    act(() => useContextStore.setState({ activeTab: 'inquiry' }))
    view.rerender(open())
    expect(screen.getByText('比较证据小组 · 论证空间')).toBeTruthy()
    submit('论证下一步怎么做？')
    await waitFor(() => expect(aiService.streamChat).toHaveBeenCalledTimes(2))
    expect(vi.mocked(aiService.streamChat).mock.calls[1][0]).toMatchObject({ conversation_id: 'thread-a', active_tab: 'inquiry' })
    expect(vi.mocked(aiService.streamChat).mock.calls[1][0].selected_document_id).toBeUndefined()
})

it('opens saved messages from personal history and does not automatically resend an interrupted turn', async () => {
    vi.mocked(aiService.getConversations).mockResolvedValue({ total: 1, conversations: [
        { id: 'old-thread', project_id: projectId, title: '比较资料', updated_at: '2026-11-27T00:00:00Z' },
    ] })
    vi.mocked(aiService.getMessages).mockResolvedValue({ total: 1, messages: [
        { id: 'm1', role: 'assistant', content: '已保存的旧回应', created_at: '2026-11-27T00:00:00Z' },
    ] })
    vi.mocked(aiService.streamChat).mockImplementation(async (_, handlers) => {
        handlers?.onError?.({ message: 'Interrupted' })
        return ''
    })
    render(open())
    fireEvent.click(screen.getByRole('button', { name: '历史对话' }))
    fireEvent.click(await screen.findByText('比较资料', { exact: true }))
    await screen.findByText('已保存的旧回应')
    submit('再追问一次')
    await screen.findByText(/未能确认回应/)
    expect(aiService.streamChat).toHaveBeenCalledTimes(1)
    expect(vi.mocked(aiService.streamChat).mock.calls[0][0].conversation_id).toBe('old-thread')
})
