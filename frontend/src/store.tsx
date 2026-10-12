import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, ApiError, errorText } from './api'
import type { AppState, ChatMessage, ChatReply, ChatScope, MaterialTarget, Meta, PrepTarget } from './types'

type Theme = 'light' | 'dark'
interface Toast { id: number; text: string; undo?: () => void }

interface Ctx {
  state: AppState
  live: boolean
  meta: Meta | null
  version: number
  invalidate: () => void
  theme: Theme
  toggleTheme: () => void
  wide: boolean
  toast: Toast | null
  showToast: (text: string, undo?: () => void) => void
  hideToast: () => void
  openMenu: string | null
  setOpenMenu: (key: string | null) => void
  chat: {
    open: boolean; scope: ChatScope; messages: ChatMessage[]; busy: boolean
    openChat: (scope?: ChatScope, question?: string) => void
    closeChat: () => void
    setScope: (scope: ChatScope) => void
    send: (text: string) => void
    clear: () => void
  }
  currentRecord: string | null
  setCurrentRecord: (id: string | null) => void
  settingsOpen: boolean
  setSettingsOpen: (v: boolean) => void
  storageOpen: boolean
  setStorageOpen: (v: boolean) => void
  /** окно «Подготовка к встрече»: по проекту или по одной записи */
  prep: PrepTarget | null
  setPrep: (v: PrepTarget | null) => void
  /** окно «Материалы»: протокол, карта, презентация */
  material: MaterialTarget | null
  setMaterial: (v: MaterialTarget | null) => void
}

const EMPTY_STATE: AppState = { queue: [], running: null, inboxCount: 0, dataVersion: 0, jobsVersion: 0, indexState: 'loading' }
const AppContext = createContext<Ctx | null>(null)

export function useApp(): Ctx {
  const ctx = useContext(AppContext)
  if (!ctx) throw new Error('AppProvider отсутствует')
  return ctx
}

function readTheme(): Theme {
  try {
    const saved = localStorage.getItem('md-theme')
    if (saved === 'light' || saved === 'dark') return saved
  } catch { /* хранилище недоступно */ }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

let messageId = 0

export function AppProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AppState>(EMPTY_STATE)
  const [live, setLive] = useState(false)
  const [meta, setMeta] = useState<Meta | null>(null)
  const [local, setLocal] = useState(0)
  const [theme, setTheme] = useState<Theme>(readTheme)
  const [wide, setWide] = useState(() => window.innerWidth >= 1180)
  const [toast, setToast] = useState<Toast | null>(null)
  const [openMenu, setOpenMenu] = useState<string | null>(null)
  const [chatOpen, setChatOpen] = useState(false)
  const [scope, setScope] = useState<ChatScope>({ kind: 'all' })
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [busy, setBusy] = useState(false)
  const [currentRecord, setCurrentRecord] = useState<string | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [storageOpen, setStorageOpen] = useState(false)
  const [prep, setPrep] = useState<PrepTarget | null>(null)
  const [material, setMaterial] = useState<MaterialTarget | null>(null)
  const toastTimer = useRef<number>()
  const messagesRef = useRef(messages)
  messagesRef.current = messages
  const scopeRef = useRef(scope)
  scopeRef.current = scope
  const busyRef = useRef(false)

  // --- тема
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('md-theme', theme) } catch { /* ничего страшного */ }
  }, [theme])
  const toggleTheme = useCallback(() => setTheme(t => (t === 'dark' ? 'light' : 'dark')), [])

  // --- адаптивность
  useEffect(() => {
    const onResize = () => setWide(window.innerWidth >= 1180)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // --- SSE с запасным опросом
  useEffect(() => {
    let source: EventSource | null = null
    let poll: number | undefined
    let closed = false
    const apply = (next: AppState) => { setState(next); setLive(true) }
    const startPolling = () => {
      if (poll) return
      poll = window.setInterval(async () => {
        try { apply(await api.get<AppState>('/api/state')) } catch { setLive(false) }
      }, 3000)
    }
    const connect = () => {
      if (closed) return
      source = new EventSource('/api/stream')
      source.addEventListener('state', e => {
        try { apply(JSON.parse((e as MessageEvent).data)) } catch { /* битый кадр */ }
        if (poll) { clearInterval(poll); poll = undefined }
      })
      source.onerror = () => { setLive(false); startPolling() }
    }
    api.get<AppState>('/api/state').then(apply).catch(() => setLive(false))
    connect()
    return () => { closed = true; source?.close(); if (poll) clearInterval(poll) }
  }, [])

  const version = state.dataVersion * 1000 + state.jobsVersion + local * 1e6
  const invalidate = useCallback(() => setLocal(v => v + 1), [])

  // --- справочники
  useEffect(() => {
    let alive = true
    api.get<Meta>('/api/meta').then(m => { if (alive) setMeta(m) }).catch(() => undefined)
    return () => { alive = false }
  }, [state.dataVersion, local])

  // --- тосты
  const hideToast = useCallback(() => { clearTimeout(toastTimer.current); setToast(null) }, [])
  const showToast = useCallback((text: string, undo?: () => void) => {
    clearTimeout(toastTimer.current)
    setToast({ id: Date.now(), text, undo })
    toastTimer.current = window.setTimeout(() => setToast(null), 3800)
  }, [])

  // --- чат
  const send = useCallback(async (raw: string) => {
    const text = raw.trim()
    if (!text || busyRef.current) return
    busyRef.current = true
    setBusy(true)
    const userMsg: ChatMessage = { id: ++messageId, role: 'user', text }
    const pending: ChatMessage = { id: ++messageId, role: 'bot', text: 'Ищу в записях…', pending: true }
    setMessages(m => [...m, userMsg, pending])
    const lastBot = [...messagesRef.current].reverse().find(m => m.role === 'bot' && m.sources?.length)
    const previous = [...new Set((lastBot?.sources ?? []).map(s => s.meetingId).filter(Boolean))]
    const current = scopeRef.current
    const payload = current.kind === 'all' ? { kind: 'all' }
      : current.kind === 'record' ? { kind: 'record', id: current.id }
      : current.kind === 'project' ? { kind: 'project', id: current.id, title: current.title }
      : current.kind === 'person' ? { kind: 'person', id: current.id }
      : { kind: 'days', days: current.days }
    try {
      const reply = await api.post<ChatReply>('/api/chat', { question: text, scope: payload, previous })
      setMessages(m => m.map(x => x.id === pending.id ? { ...x, text: reply.answer, sources: reply.sources, digest: reply.digest, material: reply.material, pending: false } : x))
    } catch (error) {
      const message = error instanceof ApiError && error.status === 503 ? error.message : 'Не удалось получить ответ. ' + errorText(error)
      setMessages(m => m.map(x => x.id === pending.id ? { ...x, text: message, pending: false, error: true } : x))
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }, [])

  const openChat = useCallback((nextScope?: ChatScope, question?: string) => {
    if (nextScope) { setScope(nextScope); scopeRef.current = nextScope }
    setChatOpen(true)
    setOpenMenu(null)
    if (question) window.setTimeout(() => void send(question), 60)
  }, [send])

  // --- закрытие меню кликом вне открытого списка
  useEffect(() => {
    if (!openMenu) return
    const onDown = (e: MouseEvent) => {
      const target = e.target as Element | null
      if (!target?.closest('.dd.open')) setOpenMenu(null)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [openMenu])

  // --- горячие клавиши
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setChatOpen(true); setOpenMenu(null) }
      if (e.key === 'Escape') { setOpenMenu(null); setChatOpen(false); setSettingsOpen(false); setStorageOpen(false) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const value = useMemo<Ctx>(() => ({
    state, live, meta, version, invalidate, theme, toggleTheme, wide, toast, showToast, hideToast, openMenu, setOpenMenu,
    chat: {
      open: chatOpen, scope, messages, busy, openChat, closeChat: () => setChatOpen(false),
      setScope, send, clear: () => setMessages([]),
    },
    currentRecord, setCurrentRecord, settingsOpen, setSettingsOpen, storageOpen, setStorageOpen, prep, setPrep, material, setMaterial,
  }), [state, live, meta, version, invalidate, theme, toggleTheme, wide, toast, showToast, hideToast, openMenu,
    chatOpen, scope, messages, busy, openChat, send, currentRecord, settingsOpen, storageOpen, prep, material])

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>
}
