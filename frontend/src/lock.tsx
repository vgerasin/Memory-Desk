import { createContext, useCallback, useContext, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { api, ApiError, errorText, LOCKED_EVENT } from './api'
import type { LockStatus } from './types'
import { IconLock } from './components/ui'
import { tr } from './i18n'

interface LockCtx {
  status: LockStatus
  setStatus: (value: LockStatus) => void
  /** закрывает сессию: интерфейс сразу уходит под замок */
  lockNow: () => Promise<void>
}

const LockContext = createContext<LockCtx | null>(null)

export function useLock(): LockCtx {
  const ctx = useContext(LockContext)
  if (!ctx) throw new Error(tr('LockGate отсутствует'))
  return ctx
}

const CHECK_EVERY = 10_000     // как часто спрашивать сервер, не вышло ли время (фоновые запросы сессию не продлевают)
const PING_EVERY = 20_000      // «я здесь» — не чаще, чем раз в 20 секунд, пока человек что-то делает

/**
 * Стоит над всем приложением. Пока PIN не введён, под ним нет ни данных, ни разметки со списками:
 * дети не отрисовываются вовсе, поэтому на экране блокировки нечего показывать и нечего подсмотреть.
 */
export function LockGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<LockStatus | null>(null)
  const [offline, setOffline] = useState(false)
  const lastPing = useRef(0)

  const load = useCallback(async () => {
    try {
      setStatus(await api.get<LockStatus>('/api/lock/status'))
      setOffline(false)
    } catch { setOffline(true) }
  }, [])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (!offline) return
    const timer = window.setInterval(() => void load(), 2000)
    return () => window.clearInterval(timer)
  }, [offline, load])

  // 423 от любого запроса: время вышло или PIN включили в другой вкладке
  useEffect(() => {
    const onLocked = () => setStatus(s => (s ? { ...s, enabled: true, locked: true } : s))
    window.addEventListener(LOCKED_EVENT, onLocked)
    return () => window.removeEventListener(LOCKED_EVENT, onLocked)
  }, [])

  const enabled = !!status?.enabled
  const locked = !!status?.locked
  useEffect(() => {
    if (!enabled || locked) return
    const check = () => void load()
    const timer = window.setInterval(check, CHECK_EVERY)
    document.addEventListener('visibilitychange', check)
    const alive = () => {
      const at = Date.now()
      if (at - lastPing.current < PING_EVERY) return
      lastPing.current = at
      api.post('/api/lock/ping').catch(() => undefined)
    }
    const events = ['pointerdown', 'keydown', 'wheel'] as const
    events.forEach(name => window.addEventListener(name, alive, { passive: true }))
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', check)
      events.forEach(name => window.removeEventListener(name, alive))
    }
  }, [enabled, locked, load])

  const lockNow = useCallback(async () => {
    try { setStatus(await api.post<LockStatus>('/api/lock/lock')) } catch { setStatus(s => (s ? { ...s, locked: true } : s)) }
  }, [])

  if (!status) {
    return <div className="lockscreen"><div className="lock-card"><div className="lock-mark"><IconLock size={26} /></div>
      <div className="lock-title">Memory Desk</div>
      <div className="lock-sub">{offline ? tr('Нет связи с Memory Desk. Проверьте, что приложение запущено. Переподключаюсь…') : tr('Загрузка…')}</div></div></div>
  }
  if (status.locked) return <LockScreen status={status} onOpen={setStatus} />
  return <LockContext.Provider value={{ status, setStatus, lockNow }}>{children}</LockContext.Provider>
}

function LockScreen({ status, onOpen }: { status: LockStatus; onOpen: (value: LockStatus) => void }) {
  const [pin, setPin] = useState('')
  const [error, setError] = useState('')
  const [wait, setWait] = useState(status.retryAfter)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (wait <= 0) return
    const timer = window.setTimeout(() => setWait(w => Math.max(0, w - 1)), 1000)
    return () => window.clearTimeout(timer)
  }, [wait])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy || wait > 0 || !pin) return
    setBusy(true); setError('')
    try {
      onOpen(await api.post<LockStatus>('/api/lock/unlock', { pin }))
    } catch (e) {
      setPin('')
      setError(errorText(e))
      if (e instanceof ApiError) setWait(Number(e.data.retryAfter) || 0)
    } finally { setBusy(false) }
  }

  return (
    <div className="lockscreen">
      <form className="lock-card" onSubmit={e => void submit(e)}>
        <div className="lock-mark"><IconLock size={26} /></div>
        <div className="lock-title">{tr('Memory Desk заблокирован')}</div>
        <div className="lock-sub">{tr('Введите PIN, чтобы продолжить')}</div>
        <input className="lock-input" type="password" inputMode="numeric" autoComplete="off" autoFocus maxLength={status.maxLength}
          value={pin} onChange={e => setPin(e.target.value.replace(/\D/g, ''))} aria-label="PIN" placeholder="••••" />
        {wait > 0
          ? <div className="lock-error" role="alert">{tr('Слишком много попыток. Подождите {0} с', [wait])}</div>
          : error && <div className="lock-error" role="alert">{error}</div>}
        <button className="btn accent s34" type="submit" disabled={busy || wait > 0 || pin.length < status.minLength}>{tr('Открыть')}</button>
        <div className="lock-help">{tr('Забыли PIN? Закройте окно и запустите «Сбросить PIN.command» из папки программы: заметки и записи останутся на месте.')}</div>
      </form>
    </div>
  )
}
