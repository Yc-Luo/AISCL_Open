// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChatMessage } from '../../../../modules/chat/ChatPersistence'
import ChatPanel from './ChatPanel'

const { get, sync, getProject } = vi.hoisted(() => ({
  get: vi.fn(),
  sync: vi.fn(),
  getProject: vi.fn(),
}))

vi.mock('../../../../services/api/client', () => ({ default: { get } }))
vi.mock('../../../../hooks/chat/useChatSync', () => ({ useChatSync: sync }))
vi.mock('../../../../services/api/project', () => ({ projectService: { getProject } }))
vi.mock('../../../../services/api/user', () => ({ userService: {} }))
vi.mock('../../../../services/api/storage', () => ({ storageService: {} }))
vi.mock('../../../../services/sync/SyncService', () => ({ syncService: { on: vi.fn(), off: vi.fn() } }))
vi.mock('../../../../services/tracking/TrackingService', () => ({ trackingService: {} }))
vi.mock('../../../../stores/authStore', () => ({ useAuthStore: () => ({ user: { id: 'me', username: 'Me' } }) }))
vi.mock('../../../../stores/presenceStore', () => ({ usePresenceStore: () => true }))
vi.mock('../../../../stores/contextStore', () => ({ useContextStore: () => undefined }))
vi.mock('../ai/AIResponseRenderer', () => ({ default: () => null }))

const created_at = '2026-01-01T00:00:00.000Z'
const saved = (id: string, client_message_id = id) => ({
  id, client_message_id, created_at, user_id: 'peer', username: 'Peer',
  content: id, message_type: 'text', mentions: [],
})
const page = (messages: ReturnType<typeof saved>[], next_cursor: string | null) => ({
  data: { messages, next_cursor },
})
const pendingPage = () => {
  let resolve!: (value: ReturnType<typeof page>) => void
  const promise = new Promise<ReturnType<typeof page>>(done => { resolve = done })
  return { promise, resolve }
}
const rows = (container: HTMLElement) =>
  Array.from(container.querySelectorAll<HTMLElement>('[data-chat-message-key]'))
const rowKeys = (container: HTMLElement) => rows(container).map(row => row.dataset.chatMessageKey)

beforeEach(() => {
  get.mockReset()
  sync.mockReset().mockReturnValue({ messages: [], connected: true })
  getProject.mockResolvedValue({ members: [] })
  Object.defineProperty(HTMLElement.prototype, 'scrollTo', { configurable: true, value: vi.fn() })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('project chat history', () => {
  it('loads one older page at a time, keeps the visible row fixed, and merges realtime overlap', async () => {
    const older = pendingPage()
    get.mockResolvedValueOnce(page([saved('03', 'live-3'), saved('02')], 'before-02'))
      .mockReturnValueOnce(older.promise)
    const { container, rerender } = render(<ChatPanel projectId="a" />)
    await waitFor(() => expect(rowKeys(container)).toEqual(['02', 'live-3']))
    const list = rows(container)[0].parentElement!
    Object.defineProperties(list, { clientHeight: { value: 200 }, scrollHeight: { value: 600 } })
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const index = rows(container).indexOf(this)
      const top = index < 0 ? 0 : index * 60 - list.scrollTop
      return { top, bottom: top + 60 } as DOMRect
    })
    list.scrollTop = 20
    fireEvent.scroll(list)
    fireEvent.scroll(list)
    expect(get).toHaveBeenCalledTimes(2)
    expect(get.mock.calls[1][1].params.cursor).toBe('before-02')

    const live: ChatMessage = {
      ...saved('live-3'), timestamp: created_at, content: 'updated',
    }
    sync.mockReturnValue({ messages: [live], connected: true })
    rerender(<ChatPanel projectId="a" />)
    await act(async () => older.resolve(page([saved('02'), saved('01')], null)))
    expect(rowKeys(container)).toEqual(['01', '02', 'live-3'])
    expect(list.scrollTop).toBe(80)
    expect(container.textContent).toContain('updated')
    fireEvent.scroll(list)
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('aborts old room requests and ignores a late response after switching rooms', async () => {
    const older = pendingPage()
    get.mockResolvedValueOnce(page([saved('a-latest')], 'before-a'))
      .mockReturnValueOnce(older.promise)
      .mockResolvedValueOnce(page([saved('b-latest')], null))
    const { container, rerender, getByRole } = render(<ChatPanel projectId="a" />)
    fireEvent.click(await waitFor(() => getByRole('button', { name: '加载较早消息' })))
    const signal: AbortSignal = get.mock.calls[1][1].signal
    rerender(<ChatPanel projectId="b" />)
    expect(signal.aborted).toBe(true)
    await act(async () => older.resolve(page([saved('a-older')], null)))
    expect(rowKeys(container)).toEqual(['b-latest'])
    expect(get.mock.calls[2][0]).toBe('/chat/projects/b/messages')
  })

  it('keeps the older cursor on reconnect and does not count historical replay as unread', async () => {
    const onUnreadChange = vi.fn()
    get.mockResolvedValueOnce(page([saved('02')], 'before-02'))
      .mockResolvedValueOnce(page([saved('03'), saved('02')], 'before-03'))
      .mockResolvedValueOnce(page([saved('01')], null))
    sync.mockReturnValue({ messages: [], connected: false })
    const panel = () => <ChatPanel projectId="a" isActive={false} onUnreadChange={onUnreadChange} />
    const { container, rerender, getByRole } = render(panel())
    await waitFor(() => expect(rowKeys(container)).toEqual(['02']))
    sync.mockReturnValue({ messages: [], connected: true })
    rerender(panel())
    await waitFor(() => expect(rowKeys(container)).toEqual(['02', '03']))
    fireEvent.click(getByRole('button', { name: '加载较早消息' }))
    expect(get.mock.calls[2][1].params.cursor).toBe('before-02')
    await waitFor(() => expect(rowKeys(container)).toEqual(['01', '02', '03']))
    sync.mockReturnValue({ messages: [{ ...saved('01'), timestamp: created_at }], connected: true })
    rerender(panel())
    expect(onUnreadChange).toHaveBeenCalledTimes(1)
    expect(rowKeys(container)).toEqual(['01', '02', '03'])
  })

  it('can page across a gap after a disjoint reconnect even if the original history was exhausted', async () => {
    get.mockResolvedValueOnce(page([saved('01')], null))
      .mockResolvedValueOnce(page([saved('03')], 'before-03'))
      .mockResolvedValueOnce(page([saved('02'), saved('01')], null))
    sync.mockReturnValue({ messages: [], connected: false })
    const { container, rerender, getByRole } = render(<ChatPanel projectId="a" />)
    await waitFor(() => expect(rowKeys(container)).toEqual(['01']))
    sync.mockReturnValue({ messages: [], connected: true })
    rerender(<ChatPanel projectId="a" />)
    fireEvent.click(await waitFor(() => getByRole('button', { name: '加载较早消息' })))
    expect(get.mock.calls[2][1].params.cursor).toBe('before-03')
    await waitFor(() => expect(rowKeys(container)).toEqual(['01', '02', '03']))
  })
})
