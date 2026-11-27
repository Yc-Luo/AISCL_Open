import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Bot, Download, FileText, History, Lightbulb, Loader2, Plus, Send, SquarePen, Trash2, X } from 'lucide-react'
import { aiService, type AIAttachment, type AICitation, type AIConversationSummary, type AIHistoryMessage, type AIStreamMeta } from '../../../../services/api/ai'
import { trackingService } from '../../../../services/tracking/TrackingService'
import { useScrapbookActions } from '../../../../modules/inquiry/hooks/useScrapbookActions'
import type { ExperimentVersion } from '../../../../types'
import { getTabLabel, normalizeStageId } from '../../../../lib/stageModel'
import { roleKeyToPreferredSubagent, type ScaffoldRoleKey } from '../../../../lib/experimentScaffold'
import { useContextStore } from '../../../../stores/contextStore'
import { Toast } from '../../../ui/Toast'
import ResourceImage from '../../../ui/ResourceImage'
import AIResponseRenderer from './AIResponseRenderer'

interface AITutorProps {
    projectId: string
    projectName?: string
    experimentVersion?: ExperimentVersion | null
    isOpen: boolean
    initialConversationId?: string
    onClose: () => void
    selectedText?: string
}

type Message = AIHistoryMessage & { interrupted?: boolean }
type ReadRequest = { id: string; before?: number }

const HISTORY_PAGE = 30
const MESSAGE_PAGE = 50
const MAX_ATTACHMENTS = 3
const MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
const ACCEPTED_ATTACHMENTS = '.txt,.md,.markdown,.csv,.json,.pdf,.docx,.xlsx,.pptx,.png,.jpg,.jpeg,.webp,.gif'
const iconButton = 'grid h-8 w-8 shrink-0 place-items-center rounded-md text-slate-600 hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:opacity-40'
const actionButton = 'rounded-md px-2 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-50 disabled:opacity-40'

function tutorRole(text: string, stage: string | null): ScaffoldRoleKey {
    if (/反驳|质疑|反例|漏洞|争议|挑战|另一种解释/.test(text)) return 'viewpoint_challenge'
    if (/修改|优化|改进|修订|反馈|标准|充分吗|学习重点/.test(text)) return 'feedback_prompting'
    if (/资料|证据|出处|来源|文献|概念|查找|搜索|依据/.test(text)) return 'cognitive_support'
    if (/下一步|推进|计划|步骤|任务|分工|进展|梳理/.test(text)) return 'problem_progression'
    const stageId = normalizeStageId(stage)
    if (stageId === 'meaning_exploration') return 'cognitive_support'
    if (stageId === 'explanation_integration') return 'viewpoint_challenge'
    if (stageId === 'application_solution') return 'feedback_prompting'
    return 'problem_progression'
}

function displayText(raw: string) {
    return raw.replace(/<think>[\s\S]*?<\/think>/gi, '').replace(/<think>[\s\S]*$/i, '').replace(/<\/?think>/gi, '')
}

async function readLatestMessages(id: string, signal: AbortSignal) {
    // The existing endpoint paginates oldest-first. Use the total to fetch the tail.
    const first = await aiService.getMessages(id, { skip: 0, limit: MESSAGE_PAGE }, signal)
    const skip = Math.max(0, first.total - MESSAGE_PAGE)
    if (!skip || signal.aborted) return { ...first, skip: 0 }
    const tail = await aiService.getMessages(id, { skip, limit: MESSAGE_PAGE }, signal)
    return { ...tail, skip }
}

export default function AITutor(props: AITutorProps) {
    // A closed panel stays mounted, but a different project never inherits its state.
    return <TutorSession key={props.projectId} {...props} />
}

function TutorSession({ projectId, projectName, experimentVersion, isOpen, initialConversationId, onClose, selectedText }: AITutorProps) {
    const [conversationId, setConversationId] = useState<string>()
    const [messages, setMessages] = useState<Message[]>([])
    const [draft, setDraft] = useState('')
    const [attachments, setAttachments] = useState<AIAttachment[]>([])
    const [isTyping, setIsTyping] = useState(false)
    const [turnStatus, setTurnStatus] = useState('')
    const [failedTurn, setFailedTurn] = useState<{ content: string; attachments: AIAttachment[] }>()
    const [isUploading, setIsUploading] = useState(false)
    const [attachmentError, setAttachmentError] = useState('')
    const [preview, setPreview] = useState<AIAttachment>()
    const [isHistoryOpen, setIsHistoryOpen] = useState(false)
    const [historyPage, setHistoryPage] = useState(0)
    const [history, setHistory] = useState<{ conversations: AIConversationSummary[]; total: number }>()
    const [historyError, setHistoryError] = useState(false)
    const [historyRevision, setHistoryRevision] = useState(0)
    const [handledLink, setHandledLink] = useState<string>()
    const [linkError, setLinkError] = useState('')
    const [readRequest, setReadRequest] = useState<ReadRequest>()
    const [readError, setReadError] = useState<ReadRequest>()
    const [olderOffset, setOlderOffset] = useState(0)
    const [citationLookup, setCitationLookup] = useState<{ conversationId: string; messageId: string }>()
    const [deleteId, setDeleteId] = useState<string>()
    const [isDeleting, setIsDeleting] = useState(false)
    const [toast, setToast] = useState('')
    const [previewReturnError, setPreviewReturnError] = useState('')
    const inputRef = useRef<HTMLTextAreaElement>(null)
    const fileInputRef = useRef<HTMLInputElement>(null)
    const scrollRef = useRef<HTMLDivElement>(null)
    const afterPrepend = useRef<number>()
    const wantsBottom = useRef(true)
    const streamRequest = useRef<AbortController>()
    const uploadRequest = useRef<AbortController>()
    const downloadRequest = useRef<AbortController>()
    const sendLock = useRef(false)
    const { addMaterial } = useScrapbookActions(projectId)
    const activeTool = useContextStore(state => state.projectId === projectId ? state.activeTab : null)
    const isMultiAgent = experimentVersion?.ai_scaffold_mode === 'multi_agent'
    const pendingLink = Boolean(initialConversationId && initialConversationId !== handledLink)
    const canNavigate = !isTyping && !isUploading && !isDeleting
    const canSend = isOpen && canNavigate && !pendingLink && !readRequest && !(readError && readError.before === undefined)
    const containsPrivateFiles = messages.some(message => message.attachments?.length || message.private_context)

    useEffect(() => () => {
        streamRequest.current?.abort()
        uploadRequest.current?.abort()
        downloadRequest.current?.abort()
    }, [])

    useEffect(() => {
        if (!isOpen || !isHistoryOpen) return
        const controller = new AbortController()
        setHistory(undefined)
        setHistoryError(false)
        void aiService.getConversations(projectId, { skip: historyPage * HISTORY_PAGE, limit: HISTORY_PAGE }, controller.signal)
            .then(page => {
                if (!controller.signal.aborted) setHistory(page)
            })
            .catch(() => {
                if (!controller.signal.aborted) setHistoryError(true)
            })
        return () => controller.abort()
    }, [isOpen, isHistoryOpen, historyPage, historyRevision, projectId])

    useEffect(() => {
        if (!isOpen || !canNavigate || !pendingLink || !initialConversationId) return
        const controller = new AbortController()
        const resolveLink = async () => {
            // The messages API accepts a personal conversation ID from any project.
            // Verify its project via the scoped list before reading any message.
            const limit = 100
            for (let skip = 0; !controller.signal.aborted; skip += limit) {
                const page = await aiService.getConversations(projectId, { skip, limit }, controller.signal)
                if (controller.signal.aborted) return
                if (page.conversations.some(item => item.id === initialConversationId && item.project_id === projectId)) {
                    if (initialConversationId !== conversationId) {
                        setConversationId(initialConversationId)
                        setMessages([])
                        setOlderOffset(0)
                        setReadError(undefined)
                        setFailedTurn(undefined)
                        setCitationLookup(undefined)
                        setReadRequest({ id: initialConversationId })
                        wantsBottom.current = true
                    }
                    return
                }
                if (!page.conversations.length || skip + limit >= page.total) break
            }
            if (!controller.signal.aborted) setLinkError('这个对话不属于当前小组，或已被删除。')
        }
        setLinkError('')
        void resolveLink().catch(() => {
            if (!controller.signal.aborted) setLinkError('未能打开历史对话，请在历史列表中重试。')
        }).finally(() => {
            if (!controller.signal.aborted) setHandledLink(initialConversationId)
        })
        return () => controller.abort()
    }, [isOpen, canNavigate, pendingLink, initialConversationId, conversationId, projectId])

    useEffect(() => {
        if (!isOpen || !readRequest) return
        const controller = new AbortController()
        const { id, before } = readRequest
        const skip = before === undefined ? 0 : Math.max(0, before - MESSAGE_PAGE)
        const load = before === undefined
            ? readLatestMessages(id, controller.signal)
            : aiService.getMessages(id, { skip, limit: before - skip }, controller.signal).then(result => ({ ...result, skip }))
        void load.then(page => {
            if (controller.signal.aborted) return
            const pageMessages = page.messages.filter(message => message.role !== 'system')
            if (before === undefined) {
                wantsBottom.current = true
                setMessages(pageMessages)
            } else {
                const scroll = scrollRef.current
                if (scroll) afterPrepend.current = scroll.scrollHeight - scroll.scrollTop
                setMessages(current => [
                    ...pageMessages.filter(item => !current.some(existing => existing.id === item.id)),
                    ...current,
                ])
            }
            setOlderOffset(page.skip)
            setReadError(undefined)
        }).catch(() => {
            if (!controller.signal.aborted) setReadError(readRequest)
        }).finally(() => {
            if (!controller.signal.aborted) setReadRequest(undefined)
        })
        return () => controller.abort()
    }, [isOpen, readRequest])

    useEffect(() => {
        if (!isOpen || !citationLookup) return
        const controller = new AbortController()
        const { conversationId: id, messageId } = citationLookup
        void readLatestMessages(id, controller.signal).then(({ messages: saved }) => {
            if (controller.signal.aborted) return
            const citations = saved.find(message => message.id === messageId)?.citations
            if (citations?.length) setMessages(current => current.map(message => message.id === messageId ? { ...message, citations } : message))
        }).catch(() => {
            // A failed citation read does not invalidate the completed conversation turn.
        }).finally(() => {
            if (!controller.signal.aborted) setCitationLookup(undefined)
        })
        return () => controller.abort()
    }, [isOpen, citationLookup])

    useLayoutEffect(() => {
        const scroll = scrollRef.current
        if (!isOpen || !scroll) return
        if (afterPrepend.current !== undefined) {
            scroll.scrollTop = scroll.scrollHeight - afterPrepend.current
            afterPrepend.current = undefined
        } else if (wantsBottom.current) {
            scroll.scrollTop = scroll.scrollHeight
        }
    }, [isOpen, messages, isTyping])

    const selectConversation = (id?: string) => {
        if (!isOpen || !canNavigate || sendLock.current) return
        setHandledLink(initialConversationId)
        setLinkError('')
        setCitationLookup(undefined)
        setConversationId(id)
        setMessages([])
        setOlderOffset(0)
        setReadError(undefined)
        setReadRequest(id ? { id } : undefined)
        setFailedTurn(undefined)
        setDraft('')
        setAttachments([])
        setAttachmentError('')
        setPreview(undefined)
        setIsHistoryOpen(false)
        wantsBottom.current = true
        if (!id) inputRef.current?.focus()
    }

    const openHistory = () => {
        setHistoryPage(0)
        setIsHistoryOpen(true)
    }

    const deleteConversation = async () => {
        if (!isOpen || !deleteId || !canNavigate || sendLock.current) return
        const id = deleteId
        setIsDeleting(true)
        try {
            await aiService.deleteConversation(id)
            if (conversationId === id) selectConversation()
            setHistoryPage(0)
            setHistoryRevision(value => value + 1)
        } catch {
            setToast('删除对话失败')
        } finally {
            setIsDeleting(false)
            setDeleteId(undefined)
        }
    }

    const uploadFiles = async (files: File[]) => {
        if (!isOpen || !canSend || !files.length || uploadRequest.current || sendLock.current) return
        const selected = files.slice(0, MAX_ATTACHMENTS - attachments.length)
        if (!selected.length) return
        const controller = new AbortController()
        uploadRequest.current = controller
        setAttachmentError('')
        setIsUploading(true)
        const eligible = selected.filter(file => file.size > 0 && file.size <= MAX_ATTACHMENT_BYTES)
        try {
            const results = await Promise.allSettled(eligible.map(file => aiService.uploadAttachment(projectId, file, controller.signal)))
            if (controller.signal.aborted) return
            const uploaded = results.flatMap(result => result.status === 'fulfilled' ? [result.value] : [])
            setAttachments(current => [...current, ...uploaded])
            if (uploaded.length < selected.length) {
                setAttachmentError('有附件未添加。请检查文件格式、网络连接及大小（每个不超过 50 MB）。')
            } else if (selected.length < files.length) {
                setAttachmentError('每条消息最多添加 3 个附件。')
            }
        } finally {
            uploadRequest.current = undefined
            if (!controller.signal.aborted) setIsUploading(false)
        }
    }

    const downloadAttachment = async (attachment: AIAttachment) => {
        if (!isOpen) return
        downloadRequest.current?.abort()
        const controller = new AbortController()
        downloadRequest.current = controller
        setPreviewReturnError('')
        try {
            const blob = await aiService.getAttachment(attachment.id, controller.signal)
            if (controller.signal.aborted) return
            const url = URL.createObjectURL(blob)
            const link = document.createElement('a')
            link.href = url
            link.download = attachment.filename
            link.click()
            setTimeout(() => URL.revokeObjectURL(url), 1000)
        } catch {
            if (!controller.signal.aborted) setPreviewReturnError('下载失败，请重试。')
        }
    }

    const send = async (event: FormEvent) => {
        event.preventDefault()
        if (!canSend || sendLock.current || uploadRequest.current || (!draft.trim() && !attachments.length)) return
        sendLock.current = true
        const controller = new AbortController()
        streamRequest.current = controller
        const content = draft.trim()
        const files = attachments
        const context = useContextStore.getState()
        const contextMatches = context.projectId === projectId
        const stage = contextMatches ? context.currentStage : null
        const role = tutorRole(content, stage)
        let sessionId = conversationId
        const hadSession = Boolean(sessionId)
        const timestamp = new Date().toISOString()
        const assistantId = `local-reply-${timestamp}`
        const userMessage: Message = { id: `local-question-${timestamp}`, role: 'user', content, created_at: timestamp, attachments: files }
        let doneMeta: AIStreamMeta | undefined
        let gotError = false
        const updateReply = (patch: Partial<Message>) => {
            if (!controller.signal.aborted) setMessages(current => current.map(message => message.id === assistantId ? { ...message, ...patch } : message))
        }
        const recordSession = (meta: AIStreamMeta) => {
            if (!controller.signal.aborted && !sessionId && meta.conversation_id) {
                sessionId = meta.conversation_id
                setConversationId(sessionId)
            }
        }
        wantsBottom.current = true
        setMessages(current => [...current, userMessage, { id: assistantId, role: 'assistant', content: '', created_at: timestamp }])
        setDraft('')
        setAttachments([])
        setAttachmentError('')
        setFailedTurn(undefined)
        setIsTyping(true)
        setTurnStatus('正在等待回应…')
        trackingService.trackResearchEvent({
            project_id: projectId, experiment_version_id: experimentVersion?.version_name || undefined,
            actor_type: 'student', event_domain: 'scaffold', event_type: 'scaffold_request', stage_id: stage || undefined,
            payload: {
                scaffold_layer: 'process_scaffold', scaffold_role: role, trigger_source: 'manual_call', trigger_reason: 'tutor_chat',
                current_stage: stage, conversation_id: sessionId, has_attachment: files.length > 0,
            },
        })
        try {
            const response = await aiService.streamChat({
                project_id: projectId,
                conversation_id: sessionId,
                role_id: 'default-tutor',
                message: content || '请结合这次上传的附件给出学习建议。',
                attachment_ids: files.length ? files.map(file => file.id) : undefined,
                context_source: 'floating_ai',
                current_stage: stage || undefined,
                active_tab: contextMatches ? context.activeTab || undefined : undefined,
                selected_document_id: contextMatches && context.activeTab === 'document' ? context.documentId || undefined : undefined,
                selected_text: contextMatches ? selectedText?.trim().slice(0, 2000) || undefined : undefined,
                enabled_rule_set: experimentVersion?.enabled_rule_set || undefined,
                enabled_scaffold_roles: experimentVersion?.enabled_scaffold_roles || [],
                preferred_subagent: isMultiAgent ? roleKeyToPreferredSubagent(role) : undefined,
            }, {
                onChunk: (_chunk, full) => updateReply({ content: displayText(full) }),
                onStatus: status => {
                    if (!controller.signal.aborted) setTurnStatus(isMultiAgent && status.message ? status.message : '正在生成回应…')
                },
                onMeta: meta => {
                    recordSession(meta)
                    if (isMultiAgent && meta.ai_meta) updateReply({ ai_meta: meta.ai_meta })
                    if (meta.citations?.length) updateReply({ citations: meta.citations })
                },
                onDone: meta => {
                    recordSession(meta)
                    doneMeta = meta
                },
                onError: () => { gotError = true },
            }, controller.signal)
            if (controller.signal.aborted) return
            if (!doneMeta || gotError || !sessionId) throw new Error('AI turn was not confirmed')
            const finalContent = displayText(doneMeta.final_content ?? response).trim()
            if (!finalContent) throw new Error('AI reply was empty')
            const savedId = doneMeta.message_id || assistantId
            updateReply({
                id: savedId, content: finalContent,
                private_context: files.length > 0 || containsPrivateFiles,
                ...(doneMeta.citations?.length ? { citations: doneMeta.citations } : {}),
            })
            if (doneMeta.message_id && doneMeta.citation_count && !doneMeta.citations?.length) {
                setCitationLookup({ conversationId: sessionId, messageId: doneMeta.message_id })
            }
            if (!hadSession) {
                setHistoryPage(0)
                setHistoryRevision(value => value + 1)
            }
            trackingService.trackResearchEvent({
                project_id: projectId, experiment_version_id: experimentVersion?.version_name || undefined,
                actor_type: 'ai_tutor', event_domain: 'scaffold', event_type: 'tutor_scaffold_response', stage_id: stage || undefined,
                payload: {
                    scaffold_layer: 'process_scaffold', scaffold_role: tutorRole(finalContent, stage),
                    trigger_source: 'manual_call', trigger_reason: 'tutor_chat', response_mode: /反驳|挑战/.test(finalContent) ? 'challenge' : /建议|可以|尝试|下一步/.test(finalContent) ? 'question' : 'suggestion',
                    current_stage: stage, conversation_id: sessionId,
                },
            })
        } catch {
            if (controller.signal.aborted) return
            // A failed stream can still have written the user's turn. Never POST it again automatically.
            setMessages(current => current.flatMap(message => message.id === assistantId
                ? (message.content.trim() ? [{ ...message, interrupted: true }] : [])
                : [message]))
            setFailedTurn({ content, attachments: files })
        } finally {
            sendLock.current = false
            streamRequest.current = undefined
            if (!controller.signal.aborted) {
                setIsTyping(false)
                setTurnStatus('')
            }
        }
    }

    const canShare = (message: Message) => !containsPrivateFiles && !message.interrupted && message.content && !/\/ai\/attachments(?:\/|\b)/i.test(message.content)
    const saveToScrapbook = async (message: Message) => {
        if (!isOpen || !canShare(message)) return
        try {
            await addMaterial(message.content, '来自 AI 导师的解答', window.location.origin + window.location.pathname)
            setToast('已提取到探究空间素材池')
        } catch {
            setToast('保存失败')
        }
    }

    return (
        <section aria-label="个人 AI 导师" className="relative flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-white text-slate-900">
            <header className="z-10 flex shrink-0 items-center gap-2 border-b border-slate-200 px-3 py-2">
                <Bot className="h-5 w-5 shrink-0 text-indigo-600" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                    <h2 className="text-sm font-semibold">个人 AI 助手</h2>
                    {(projectName || activeTool) && (
                        <p className="truncate text-xs text-slate-600" title={[projectName, activeTool && getTabLabel(activeTool)].filter(Boolean).join(' · ')}>
                            {[projectName, activeTool && getTabLabel(activeTool)].filter(Boolean).join(' · ')}
                        </p>
                    )}
                </div>
                <button type="button" className={iconButton} title="新建对话" aria-label="新建对话" disabled={!canNavigate} onClick={() => selectConversation()}><SquarePen size={17} /></button>
                <button type="button" className={iconButton} title="历史对话" aria-label="历史对话" aria-expanded={isHistoryOpen} onClick={() => isHistoryOpen ? setIsHistoryOpen(false) : openHistory()}><History size={18} /></button>
                <button type="button" className={iconButton} title="收起 AI 导师" aria-label="收起 AI 导师" onClick={onClose}><X size={18} /></button>
            </header>

            <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-2.5" onScroll={event => {
                const { scrollTop, scrollHeight, clientHeight } = event.currentTarget
                wantsBottom.current = scrollHeight - scrollTop - clientHeight < 48
            }}>
                {olderOffset > 0 && <div className="text-center"><button type="button" className={actionButton} disabled={Boolean(readRequest) || isTyping} onClick={() => conversationId && setReadRequest({ id: conversationId, before: olderOffset })}>加载更早消息</button></div>}
                {pendingLink && <p className="text-sm text-slate-500" role="status">正在查找历史对话…</p>}
                {linkError && <p className="text-sm text-amber-800" role="alert">{linkError}</p>}
                {readRequest && <p className="text-center text-xs text-slate-500" role="status">正在加载消息…</p>}
                {readError && <div role="alert" className="text-sm text-amber-800">消息加载失败。<button className={actionButton} type="button" onClick={() => setReadRequest(readError)}>重试加载</button></div>}
                {!messages.length && !pendingLink && !readRequest && !readError && <p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm leading-6 text-slate-700">你好！关于小组任务、协作进展或遇到的问题，你想聊些什么？</p>}
                {messages.map(message => (
                    <article key={message.id} className={`w-fit max-w-[96%] break-words rounded-lg px-3 py-2 ${message.role === 'user' ? 'ml-auto bg-indigo-600 text-white' : 'border border-slate-200 bg-white'}`}>
                        {message.role === 'assistant' && isMultiAgent && message.ai_meta && <div className="mb-2 rounded-md border border-indigo-100 bg-indigo-50 px-2.5 py-2 text-xs leading-5 text-indigo-800">
                            <p className="font-semibold">{message.ai_meta.primary_view || message.ai_meta.primary_agent || '个人 AI 导师'}</p>
                            {message.ai_meta.rationale_summary && <p>{message.ai_meta.rationale_summary}</p>}
                            {!!message.ai_meta.processing_summary?.length && <details className="mt-1">
                                <summary className="cursor-pointer">查看本轮思考路径</summary>
                                <ul className="list-disc pl-4">{message.ai_meta.processing_summary.map((step, index) => <li key={index}>{step}</li>)}</ul>
                            </details>}
                        </div>}
                        {message.role === 'assistant' ? <AIResponseRenderer content={message.content} emptyText={turnStatus || '正在生成…'} compact /> : message.content && <div className="prose prose-sm prose-invert max-w-none text-sm !text-white [&_p]:my-1 [&_p]:whitespace-pre-wrap prose-img:max-h-48">
                            <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ img: ({ src, alt, title }) => <ResourceImage src={src} alt={alt} title={title} loading="lazy" /> }}>{message.content}</ReactMarkdown>
                        </div>}
                        {!!message.attachments?.length && <ul className="mt-1.5 space-y-1">{message.attachments.map(file => <li key={file.id}>
                            <button type="button" onClick={() => { setPreview(file); setPreviewReturnError('') }} className="flex max-w-full items-center gap-1.5 rounded-md bg-white/15 px-2 py-1 text-left text-xs hover:bg-white/25" title={`查看附件：${file.filename}`}>
                                <FileText size={14} className="shrink-0" /><span className="truncate" dir="auto">{file.filename}</span>
                            </button>
                        </li>)}</ul>}
                        <Citations citations={message.citations} />
                        {message.interrupted && <p className="text-xs text-amber-800">回应中断，以上内容未确认完整。</p>}
                        <div className={`mt-1 flex items-center gap-2 text-[11px] ${message.role === 'user' ? 'justify-end text-indigo-100' : 'text-slate-500'}`}>
                            <time dateTime={message.created_at}>{new Date(message.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time>
                            {message.role === 'assistant' && !isTyping && canShare(message) && <button type="button" className="rounded p-0.5 hover:text-indigo-600" title="存入探究空间素材池" aria-label="存入探究空间素材池" onClick={() => void saveToScrapbook(message)}><Lightbulb size={13} /></button>}
                        </div>
                    </article>
                ))}
            </div>

            {failedTurn && <div className="shrink-0 border-t border-amber-100 bg-amber-50 px-3 py-1.5 text-xs text-amber-900" role="alert">
                <p>未能确认回应。原消息可能已保存，请先核对记录再决定是否重发。</p>
                <button type="button" className={actionButton} disabled={Boolean(readRequest) || !canNavigate} onClick={() => conversationId ? setReadRequest({ id: conversationId }) : openHistory()}>核对已保存消息</button>
                <button type="button" className={actionButton} disabled={Boolean(draft || attachments.length) || !canNavigate} onClick={() => {
                    setDraft(failedTurn.content)
                    setAttachments(failedTurn.attachments)
                    setFailedTurn(undefined)
                    inputRef.current?.focus()
                }}>恢复草稿</button>
            </div>}

            <form onSubmit={send} className="shrink-0 border-t border-slate-200 px-3 py-2">
                {attachments.length > 0 && <ul className="mb-2 flex flex-wrap gap-1" aria-label="待发送附件">
                    {attachments.map(file => <li key={file.id} className="flex min-w-0 max-w-full items-center gap-1 rounded-md bg-indigo-50 px-2 py-0.5 text-xs text-indigo-800">
                        <button type="button" className="min-w-0 truncate" title={`查看附件：${file.filename}`} onClick={() => { setPreview(file); setPreviewReturnError('') }}>{file.filename}</button>
                        <button type="button" className="shrink-0 rounded p-0.5 hover:bg-indigo-100" title="移除附件" aria-label={`移除附件：${file.filename}`} onClick={() => setAttachments(current => current.filter(item => item.id !== file.id))}><X size={13} /></button>
                    </li>)}
                </ul>}
                {attachmentError && <p role="alert" className="mb-1 text-xs text-amber-800">{attachmentError}</p>}
                <div className="flex items-end gap-2">
                    <input ref={fileInputRef} type="file" multiple hidden accept={ACCEPTED_ATTACHMENTS} aria-label="选择附件" onChange={event => {
                        const files = Array.from(event.currentTarget.files || [])
                        event.currentTarget.value = ''
                        void uploadFiles(files)
                    }} />
                    <button type="button" className={iconButton} title="添加附件" aria-label="添加附件" disabled={!canSend || attachments.length >= MAX_ATTACHMENTS} onClick={() => fileInputRef.current?.click()}>
                        {isUploading ? <Loader2 className="animate-spin" size={17} /> : <Plus size={18} />}
                    </button>
                    <textarea ref={inputRef} value={draft} rows={2} maxLength={2000} onChange={event => setDraft(event.target.value)}
                        onKeyDown={event => {
                            if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229) return
                            event.preventDefault()
                            event.currentTarget.form?.requestSubmit()
                        }}
                        className="min-w-0 flex-1 resize-none rounded-md border border-slate-200 px-2 py-1.5 text-sm placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
                        aria-label="向 AI 导师提问" placeholder="向 AI 导师提问…" />
                    <button type="submit" className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40" title="发送" aria-label="发送" disabled={!canSend || (!draft.trim() && !attachments.length)}><Send size={16} /></button>
                </div>
            </form>

            {isHistoryOpen && <div className="absolute inset-x-0 bottom-0 top-[49px] z-20 flex min-h-0 flex-col bg-white" aria-label="历史对话列表">
                <div className="flex shrink-0 items-center justify-between border-b border-slate-200 px-3 py-1.5">
                    <h3 className="text-sm font-semibold">历史对话</h3>
                    <button type="button" className={iconButton} title="关闭历史" aria-label="关闭历史" onClick={() => setIsHistoryOpen(false)}><X size={16} /></button>
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
                    {history?.conversations.filter(item => item.project_id === projectId).map(item => <div key={item.id} className="flex items-center gap-1 border-b border-slate-100">
                        <button type="button" disabled={!canNavigate} aria-current={conversationId === item.id ? 'true' : undefined} className="min-w-0 flex-1 rounded-md px-2 py-1.5 text-left text-sm hover:bg-slate-100 aria-[current=true]:bg-indigo-50 disabled:opacity-50" onClick={() => selectConversation(item.id)}>
                            <span className="block truncate" title={item.title}>{item.title || '未命名对话'}</span>
                            <time dateTime={item.updated_at} className="text-xs text-slate-500">{new Date(item.updated_at).toLocaleDateString()}</time>
                        </button>
                        <button type="button" disabled={!canNavigate} className={iconButton} title="删除对话" aria-label={`删除对话：${item.title || '未命名对话'}`} onClick={() => setDeleteId(item.id)}><Trash2 size={14} /></button>
                    </div>)}
                    {history?.total === 0 && <p className="p-3 text-sm text-slate-500">暂无历史对话</p>}
                    {!history && !historyError && <p role="status" className="p-3 text-sm text-slate-500">正在加载历史…</p>}
                    {historyError && <p role="alert" className="p-3 text-sm text-amber-800">历史加载失败。<button type="button" className={actionButton} onClick={() => setHistoryRevision(value => value + 1)}>重试</button></p>}
                </div>
                {((history?.total ?? 0) > HISTORY_PAGE || historyPage > 0) && <nav aria-label="历史对话分页" className="flex shrink-0 items-center justify-between border-t border-slate-200 px-3 py-1.5">
                    <button type="button" className={actionButton} disabled={historyPage === 0} onClick={() => setHistoryPage(value => value - 1)}>上一页</button>
                    <span className="text-xs text-slate-600">第 {historyPage + 1} 页</span>
                    <button type="button" className={actionButton} disabled={(historyPage + 1) * HISTORY_PAGE >= (history?.total ?? 0)} onClick={() => setHistoryPage(value => value + 1)}>下一页</button>
                </nav>}
            </div>}

            {isOpen && preview && <AttachmentPreview key={preview.id} attachment={preview} onClose={() => setPreview(undefined)} onDownload={() => void downloadAttachment(preview)} downloadError={previewReturnError} />}
            {isOpen && deleteId && <div data-tutor-layer role="alertdialog" aria-label="删除对话" className="absolute inset-0 z-30 flex items-center justify-center bg-slate-900/30 p-3">
                <div className="w-full max-w-xs rounded-lg bg-white p-4 shadow-lg">
                    <p className="text-sm font-semibold">删除这段对话？</p>
                    <p className="mt-1 text-xs text-slate-600">删除后无法恢复。</p>
                    <div className="mt-3 flex justify-end gap-2">
                        <button type="button" className={actionButton} disabled={isDeleting} onClick={() => setDeleteId(undefined)}>取消</button>
                        <button type="button" className="rounded-md bg-red-600 px-2 py-1 text-xs text-white disabled:opacity-40" disabled={isDeleting} onClick={() => void deleteConversation()}>确认删除</button>
                    </div>
                </div>
            </div>}
            {isOpen && toast && <Toast message={toast} onClose={() => setToast('')} />}
        </section>
    )
}

function Citations({ citations }: { citations?: AICitation[] }) {
    if (!citations?.length) return null
    return <details className="mt-2 rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs text-slate-700">
        <summary className="cursor-pointer font-medium">引用来源（{citations.length}）</summary>
        <ul className="mt-1 space-y-1">{citations.map((citation, index) => <li key={index} className="flex justify-between gap-2">
            <span className="min-w-0 break-all">{citation.title || citation.resource_id || '引用资料'}</span>
            {citation.resource_type && <span className="shrink-0 text-slate-500">{citation.resource_type}</span>}
        </li>)}</ul>
    </details>
}

function AttachmentPreview({ attachment, onClose, onDownload, downloadError }: { attachment: AIAttachment; onClose: () => void; onDownload: () => void; downloadError: string }) {
    const [data, setData] = useState<{ url?: string; text?: string; error?: boolean }>()
    const image = /^image\/(?:png|jpeg|gif|webp|bmp|avif)$/i.test(attachment.mime_type)
    const pdf = attachment.mime_type === 'application/pdf'
    const plainText = /^(?:text\/(?:plain|markdown|csv)|application\/json)$/i.test(attachment.mime_type)
    const supported = image || pdf || plainText
    useEffect(() => {
        if (!supported) return
        const controller = new AbortController()
        let url: string | undefined
        void aiService.getAttachment(attachment.id, controller.signal).then(async blob => {
            if (controller.signal.aborted) return
            if (plainText) {
                const text = await blob.slice(0, 100_000).text()
                if (!controller.signal.aborted) setData({ text: blob.size > 100_000 ? `${text}\n…` : text })
            } else {
                url = URL.createObjectURL(blob.slice(0, blob.size, attachment.mime_type))
                setData({ url })
            }
        }).catch(() => {
            if (!controller.signal.aborted) setData({ error: true })
        })
        return () => {
            controller.abort()
            if (url) URL.revokeObjectURL(url)
        }
    }, [attachment.id, attachment.mime_type, plainText, supported])
    return <div data-tutor-layer className="absolute inset-0 z-30 flex min-h-0 flex-col bg-white" role="dialog" aria-label="附件预览"
        onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); onClose() } }}>
        <header className="flex shrink-0 items-center justify-between gap-1 border-b border-slate-200 px-2 py-1.5">
            <span className="min-w-0 flex-1 truncate text-sm font-semibold" title={attachment.filename}>{attachment.filename}</span>
            <button type="button" className={iconButton} title="下载附件" aria-label="下载附件" onClick={onDownload}><Download size={17} /></button>
            <button type="button" className={iconButton} title="关闭附件预览" aria-label="关闭附件预览" autoFocus onClick={onClose}><X size={17} /></button>
        </header>
        {downloadError && <p role="alert" className="px-3 pt-1 text-xs text-amber-800">{downloadError}</p>}
        {!supported ? <p className="p-3 text-sm text-slate-600">文件可下载查看。</p>
            : !data ? <p role="status" className="p-3 text-sm text-slate-500">正在加载预览…</p>
                : data.error ? <p role="alert" className="p-3 text-sm text-amber-800">预览加载失败，可尝试下载。</p>
                    : pdf ? <iframe className="min-h-0 flex-1" title={attachment.filename} src={data.url} sandbox="" />
                        : image ? <div className="min-h-0 flex-1 overflow-auto p-2"><img className="mx-auto max-h-full max-w-full object-contain" alt={attachment.filename} src={data.url} /></div>
                            : <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words p-3 text-xs">{data.text}</pre>}
    </div>
}
