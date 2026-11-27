// @vitest-environment jsdom
import { useState } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ContextualAIAssistant from './ContextualAIAssistant'

const tutorRender = vi.hoisted(() => vi.fn())
vi.mock('./AITutor', () => ({
    default: function Tutor({ initialConversationId, onClose }: { initialConversationId?: string; onClose: () => void }) {
        tutorRender()
        const [draft, setDraft] = useState('')
        return (
            <>
                <p>{initialConversationId}</p>
                <input aria-label="提问草稿" value={draft} onChange={event => setDraft(event.target.value)} />
                <button type="button">附件预览</button>
                <button type="button" onClick={onClose}>收起个人 AI 助手</button>
            </>
        )
    },
}))

function media(matches: boolean) {
    vi.stubGlobal('matchMedia', () => ({ matches, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
}

function MockWorkspace({ initial = false }: { initial?: boolean }) {
    const [open, setOpen] = useState(initial)
    return <>
        <button type="button">协作文档</button>
        <ContextualAIAssistant projectId="group-a" isOpen={open} onOpenChange={setOpen} initialConversationId="legacy-conversation" />
    </>
}

beforeEach(() => { media(false); vi.clearAllMocks() })
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('opens lazily with the requested conversation and keeps a draft after an outside click', async () => {
    render(<MockWorkspace />)
    expect(tutorRender).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '打开个人 AI 助手' }))
    const draft = await screen.findByRole<HTMLInputElement>('textbox', { name: '提问草稿' })
    expect(screen.getByText('legacy-conversation')).toBeTruthy()
    fireEvent.change(draft, { target: { value: '我想比较两条证据' } })
    fireEvent.pointerDown(screen.getByRole('button', { name: '附件预览' }))
    expect(screen.getByRole('dialog', { name: '个人 AI 助手' })).toBeTruthy()
    fireEvent.pointerDown(screen.getByRole('button', { name: '协作文档' }))
    expect(screen.queryByRole('dialog', { name: '个人 AI 助手' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '打开个人 AI 助手' }))
    expect(screen.getByRole<HTMLInputElement>('textbox', { name: '提问草稿' }).value).toBe('我想比较两条证据')
})

it('resizes by keyboard, then closes on Escape and restores the launcher focus', async () => {
    render(<MockWorkspace initial />)
    const dialog = screen.getByRole('dialog', { name: '个人 AI 助手' })
    vi.spyOn(dialog, 'getBoundingClientRect').mockReturnValue({ width: 500, height: 620 } as DOMRect)
    const resize = screen.getByRole('button', { name: '调整面板大小' })
    fireEvent.keyDown(resize, { key: 'ArrowLeft' })
    expect(dialog.style.getPropertyValue('--assistant-width')).toContain('524px')
    const draft = await screen.findByRole('textbox', { name: '提问草稿' })
    fireEvent.keyDown(draft, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '打开个人 AI 助手' }))
})

it('uses a modal without a resize handle at phone widths', async () => {
    media(true)
    render(<MockWorkspace initial />)
    const dialog = screen.getByRole('dialog', { name: '个人 AI 助手' })
    expect(dialog.getAttribute('aria-modal')).toBe('true')
    expect(screen.queryByRole('button', { name: '调整面板大小' })).toBeNull()
    fireEvent.click(await screen.findByRole('button', { name: '收起个人 AI 助手' }))
    expect(screen.queryByRole('dialog')).toBeNull()
})
