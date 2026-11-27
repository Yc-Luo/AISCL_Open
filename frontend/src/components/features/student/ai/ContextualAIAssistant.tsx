import { lazy, Suspense, useEffect, useId, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from 'react'
import { createPortal } from 'react-dom'
import { Bot, Grip, X } from 'lucide-react'
import type { ExperimentVersion } from '../../../../types'

const AITutor = lazy(() => import('./AITutor'))
const narrowScreen = '(max-width: 639px)'
const viewportMargin = 32
type Size = { width: number; height: number }

interface Props {
    projectId: string
    projectName?: string
    experimentVersion?: ExperimentVersion | null
    isOpen: boolean
    onOpenChange: (open: boolean) => void
    initialConversationId?: string
}

function clampSize(size: Size): Size {
    const maxWidth = Math.max(1, window.innerWidth - viewportMargin)
    const maxHeight = Math.max(1, window.innerHeight - viewportMargin)
    return {
        width: Math.min(maxWidth, Math.max(360, size.width)),
        height: Math.min(maxHeight, Math.max(340, size.height)),
    }
}

export default function ContextualAIAssistant({ projectId, projectName, experimentVersion, isOpen, onOpenChange, initialConversationId }: Props) {
    const id = useId()
    const launcher = useRef<HTMLButtonElement>(null)
    const panel = useRef<HTMLDivElement>(null)
    const restoreFocus = useRef(false)
    const selection = useRef('')
    const drag = useRef<{ pointerId: number; x: number; y: number; size: Size } | null>(null)
    const [hasOpened, setHasOpened] = useState(isOpen)
    const [size, setSize] = useState<Size>({ width: 500, height: 620 })
    const [mobile, setMobile] = useState(() => window.matchMedia(narrowScreen).matches)

    useEffect(() => {
        const query = window.matchMedia(narrowScreen)
        const change = (event: MediaQueryListEvent) => setMobile(event.matches)
        query.addEventListener('change', change)
        return () => query.removeEventListener('change', change)
    }, [])

    useEffect(() => {
        if (isOpen) {
            setHasOpened(true)
            panel.current?.focus({ preventScroll: true })
        } else if (restoreFocus.current) {
            restoreFocus.current = false
            launcher.current?.focus({ preventScroll: true })
        }
    }, [isOpen])

    useEffect(() => {
        if (!isOpen) return
        const dismissOutside = (event: globalThis.PointerEvent) => {
            if (!(event.target instanceof Element) || panel.current?.contains(event.target)) return
            if (event.target.closest('[data-tutor-layer]')) return
            selection.current = ''
            onOpenChange(false)
        }
        document.addEventListener('pointerdown', dismissOutside, true)
        return () => document.removeEventListener('pointerdown', dismissOutside, true)
    }, [isOpen, onOpenChange])

    const close = () => {
        selection.current = ''
        restoreFocus.current = true
        onOpenChange(false)
    }
    const onPanelKey = (event: KeyboardEvent<HTMLDivElement>) => {
        if (event.defaultPrevented || (event.target instanceof Element && event.target.closest('[data-tutor-layer]'))) return
        if (event.key === 'Escape') {
            event.preventDefault()
            close()
            return
        }
        if (!mobile || event.key !== 'Tab') return
        const controls = [...event.currentTarget.querySelectorAll<HTMLElement>(
            'button:not([disabled]), a[href], input:not([disabled]), textarea:not([disabled]), select:not([disabled]), summary, [tabindex="0"]',
        )].filter(control => control.getClientRects().length > 0)
        const first = controls[0]
        const last = controls[controls.length - 1]
        if (event.shiftKey && (document.activeElement === first || document.activeElement === event.currentTarget)) {
            event.preventDefault()
            last?.focus()
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault()
            first?.focus()
        }
    }

    const startResize = (event: PointerEvent<HTMLButtonElement>) => {
        if (event.button !== 0 || !panel.current) return
        const { width, height } = panel.current.getBoundingClientRect()
        drag.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, size: { width, height } }
        event.currentTarget.setPointerCapture(event.pointerId)
    }
    const moveResize = (event: PointerEvent<HTMLButtonElement>) => {
        const start = drag.current
        if (start?.pointerId !== event.pointerId) return
        setSize(clampSize({ width: start.size.width + start.x - event.clientX, height: start.size.height + start.y - event.clientY }))
    }
    const resizeWithKeys = (event: KeyboardEvent<HTMLButtonElement>) => {
        const dx = event.key === 'ArrowLeft' ? 24 : event.key === 'ArrowRight' ? -24 : 0
        const dy = event.key === 'ArrowUp' ? 24 : event.key === 'ArrowDown' ? -24 : 0
        if (dx === 0 && dy === 0) return
        const bounds = panel.current?.getBoundingClientRect()
        if (!bounds) return
        event.preventDefault()
        setSize(clampSize({ width: bounds.width + dx, height: bounds.height + dy }))
    }
    const captureSelection = () => {
        selection.current = window.getSelection()?.toString().trim().slice(0, 2000) || ''
    }
    const style = {
        '--assistant-width': `min(${size.width}px, calc(100vw - 2rem))`,
        '--assistant-height': `min(${size.height}px, calc(100dvh - 2rem))`,
    } as CSSProperties

    return createPortal(
        <>
            {!isOpen && (
                <button
                    ref={launcher}
                    type="button"
                    title="打开个人 AI 助手"
                    aria-label="打开个人 AI 助手"
                    aria-haspopup="dialog"
                    aria-controls={hasOpened ? id : undefined}
                    onPointerDown={captureSelection}
                    onClick={event => { if (event.detail === 0) captureSelection(); onOpenChange(true) }}
                    className="group fixed bottom-24 right-0 z-40 flex h-12 w-12 translate-x-[45%] items-center rounded-l-full border border-indigo-300 bg-indigo-700 pl-1.5 text-white shadow-md transition-transform hover:translate-x-0 focus-visible:translate-x-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 motion-reduce:transition-none"
                >
                    <Bot size={19} className="transition-transform group-hover:translate-x-2 group-focus-visible:translate-x-2" aria-hidden="true" />
                </button>
            )}
            {(isOpen || hasOpened) && (
                <div
                    id={id}
                    ref={panel}
                    hidden={!isOpen}
                    role="dialog"
                    aria-label="个人 AI 助手"
                    aria-modal={isOpen && mobile ? true : undefined}
                    tabIndex={-1}
                    onKeyDown={onPanelKey}
                    className="fixed inset-0 z-[60] min-h-0 min-w-0 bg-white outline-none sm:bottom-4 sm:left-auto sm:right-4 sm:top-auto sm:h-[var(--assistant-height)] sm:w-[var(--assistant-width)] sm:rounded-lg sm:border sm:border-slate-200 sm:shadow-xl"
                    style={style}
                >
                    {!mobile && <button
                        type="button"
                        title="调整面板大小（方向键）"
                        aria-label="调整面板大小"
                        onPointerDown={startResize}
                        onPointerMove={moveResize}
                        onLostPointerCapture={() => { drag.current = null }}
                        onKeyDown={resizeWithKeys}
                        className="absolute left-0 top-0 z-30 hidden h-7 w-7 -translate-x-1/2 -translate-y-1/2 cursor-nwse-resize touch-none items-center justify-center rounded-md border border-slate-200 bg-white text-slate-500 shadow-sm hover:text-indigo-700 focus-visible:text-indigo-700 sm:flex"
                    >
                        <Grip size={15} className="rotate-45" aria-hidden="true" />
                    </button>}
                    <Suspense fallback={
                        <div className="flex items-center justify-between p-4 text-sm">
                            <span role="status">正在打开个人 AI 助手…</span>
                            <button type="button" aria-label="收起个人 AI 助手" onClick={close}><X size={18} /></button>
                        </div>
                    }>
                        <AITutor projectId={projectId} projectName={projectName} experimentVersion={experimentVersion} isOpen={isOpen}
                            initialConversationId={initialConversationId} onClose={close} selectedText={selection.current || undefined} />
                    </Suspense>
                </div>
            )}
        </>,
        document.body,
    )
}
