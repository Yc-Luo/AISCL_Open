// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import api from '../../../../services/api/client'
import ProjectDashboard from './ProjectDashboard'

vi.mock('../../../../services/api/client', () => ({ default: { get: vi.fn() } }))
vi.mock('../../../../services/api/project', () => ({
    projectService: {
        getProjects: async () => ({ projects: [{ id: 'group-1', name: 'Test Group' }] }),
        getExperimentVersion: async () => null,
    },
}))
vi.mock('../../../../services/tracking/TrackingService', () => ({ trackingService: {} }))

const projectPath = '/analytics/projects/group-1'
const tutorPath = `${projectPath}/ai-tutor-transcripts`
const transcriptButton = '单独导出 AI 导师私密对话全文 CSV'
const privateMessage = 'private tutor message fixture'
const groupChat = { messages: [{ content: 'group chat fixture' }], total: 1 }
const responses = new Map<string, unknown>([
    [`${projectPath}/dashboard`, null],
    [`${projectPath}/research-health`, { event_domain_counts: {}, key_event_counts: {} }],
    [`${projectPath}/research-events`, { events: [], total: 0 }],
    [`${projectPath}/group-stage-features`, { features: [], total: 0 }],
    [`${projectPath}/lsa-ready`, { sequences: [], total: 0 }],
    [`${projectPath}/group-chat-transcripts`, groupChat],
    [tutorPath, { messages: [{ content: privateMessage }], total: 1 }],
])

const createObjectURL = vi.fn((_blob: Blob) => 'blob:research-export')
const downloads: string[] = []

const readBlob = (blob: Blob) => new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error)
    reader.readAsText(blob)
})

async function openDashboard() {
    render(<MemoryRouter><ProjectDashboard /></MemoryRouter>)
    return screen.findByRole('button', { name: transcriptButton })
}

const requestedPaths = () => vi.mocked(api.get).mock.calls.map(([url]) => url)

beforeEach(() => {
    vi.mocked(api.get).mockReset().mockImplementation(async (url) => {
        if (!responses.has(url)) throw new Error(`Unexpected request: ${url}`)
        return { data: responses.get(url) }
    })
    downloads.length = 0
    createObjectURL.mockClear()
    vi.stubGlobal('URL', class extends URL {
        static createObjectURL = createObjectURL
        static revokeObjectURL = vi.fn()
    })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
        downloads.push(this.download)
    })
})

afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
})

describe('teacher research exports', () => {
    it.each(['导出常规研究数据包', '常规研究数据包 JSON'])('excludes private tutor transcripts via %s', async (name) => {
        const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
        await openDashboard()
        expect(requestedPaths()).not.toContain(tutorPath)

        fireEvent.click(screen.getByRole('button', { name }))
        await screen.findByText('已导出常规研究数据包。')
        expect(confirm).not.toHaveBeenCalled()
        expect(requestedPaths()).not.toContain(tutorPath)
        expect(downloads).toEqual([expect.stringMatching(/^research_bundle_.*\.json$/)])
        expect(createObjectURL).toHaveBeenCalledTimes(1)
        const payload = await readBlob(createObjectURL.mock.calls[0][0])
        const bundle = JSON.parse(payload)
        expect(bundle).toMatchObject({ project_id: 'group-1', group_chat_transcripts: groupChat })
        expect(bundle).not.toHaveProperty('ai_tutor_transcripts')
        expect(payload).not.toContain(privateMessage)
    })

    it('requests and downloads the separate CSV only after confirmation', async () => {
        const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
        const button = await openDashboard()
        vi.mocked(api.get).mockClear()

        fireEvent.click(button)
        expect(confirm).toHaveBeenCalledWith(expect.stringContaining('Test Group」的 AI 导师私密对话全文'))
        expect(api.get).not.toHaveBeenCalled()
        expect(createObjectURL).not.toHaveBeenCalled()
        expect(downloads).toEqual([])

        fireEvent.click(button)
        await screen.findByText('已单独导出 AI 导师私密对话全文 1 条。')
        expect(confirm).toHaveBeenCalledTimes(2)
        expect(requestedPaths()).toEqual([tutorPath])
        expect(downloads).toEqual([expect.stringMatching(/^ai_tutor_transcripts_.*\.csv$/)])
        expect(await readBlob(createObjectURL.mock.calls[0][0])).toBe(`content\n${privateMessage}`)
    })
})
