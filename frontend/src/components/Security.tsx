import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, errorText } from '../api'
import { useLock } from '../lock'
import { useApp } from '../store'
import type { AuditPage, LockStatus } from '../types'
import { localeTag, tr } from '../i18n'

/** Раздел «Безопасность» в настройках: PIN, время блокировки, вход в журнал действий. Действия применяются сразу, без «Сохранить». */
export function SecurityBlock() {
  const { status, setStatus, lockNow } = useLock()
  const { showToast, setAuditOpen, setSettingsOpen } = useApp()
  const [current, setCurrent] = useState('')
  const [pin, setPin] = useState('')
  const [again, setAgain] = useState('')
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const digits = (value: string) => value.replace(/\D/g, '')

  const reset = () => { setCurrent(''); setPin(''); setAgain(''); setOpen(false); setError('') }
  const run = async (action: () => Promise<LockStatus & { message?: string }>) => {
    setBusy(true); setError('')
    try {
      const next = await action()
      setStatus(next); showToast(next.message || tr('Готово')); reset()
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  const save = () => {
    if (pin !== again) { setError(tr('PIN и повтор не совпадают')); return }
    void run(() => api.post('/api/lock/pin', { pin, current }))
  }
  const remove = () => run(() => api.post('/api/lock/remove', { current }))
  const setTimeoutMin = async (minutes: number) => {
    try { setStatus(await api.put<LockStatus>('/api/lock/timeout', { minutes })) } catch (e) { setError(errorText(e)) }
  }
  const lengthOk = pin.length >= status.minLength && pin.length <= status.maxLength

  return (
    <div className="field security">
      <label>{tr('Безопасность')}</label>
      <div className="switch-row">
        <span className="hint">
          {status.enabled
            ? tr('PIN включён. От PIN хранится только хэш: {0}.', [status.storage === 'keychain' ? tr('в Связке ключей macOS') : tr('в файле lock.json с доступом только для вас')])
            : tr('PIN выключен. С PIN при открытии и после паузы в работе нужно вводить код, иначе интерфейс и API ничего не показывают.')}
        </span>
        {!open && (
          <button className="btn s28" onClick={() => setOpen(true)}>{status.enabled ? tr('Сменить или снять') : tr('Задать PIN')}</button>
        )}
      </div>
      {open && (
        <div className="pin-form">
          {status.enabled && (
            <input type="password" inputMode="numeric" autoComplete="off" placeholder={tr('Текущий PIN')} value={current}
              maxLength={status.maxLength} onChange={e => setCurrent(digits(e.target.value))} aria-label={tr('Текущий PIN')} />
          )}
          <input type="password" inputMode="numeric" autoComplete="off" placeholder={tr('Новый PIN ({0}–{1} цифр)', [status.minLength, status.maxLength])} value={pin}
            maxLength={status.maxLength} onChange={e => setPin(digits(e.target.value))} aria-label={tr('Новый PIN')} />
          <input type="password" inputMode="numeric" autoComplete="off" placeholder={tr('Повторите новый PIN')} value={again}
            maxLength={status.maxLength} onChange={e => setAgain(digits(e.target.value))} aria-label={tr('Повтор PIN')} />
          <div className="pin-actions">
            <button className="btn s28 muted" onClick={reset}>{tr('Отмена')}</button>
            {status.enabled && <button className="btn s28 danger" disabled={busy || !current} onClick={() => void remove()}>{tr('Снять PIN')}</button>}
            <button className="btn s28 accent" disabled={busy || !lengthOk || !again || (status.enabled && !current)} onClick={save}>
              {status.enabled ? tr('Сменить PIN') : tr('Включить PIN')}
            </button>
          </div>
        </div>
      )}
      {status.enabled && (
        <div className="backup-plan">
          <label htmlFor="lock-timeout">{tr('Блокировать после')}</label>
          <select id="lock-timeout" value={status.timeoutMin} onChange={e => void setTimeoutMin(Number(e.target.value))}>
            {status.timeouts.map(m => <option key={m} value={m}>{tr('{0} без действий', [m === 60 ? tr('1 часа') : tr('{0} мин', [m])])}</option>)}
          </select>
          <button className="btn s28" onClick={() => { setSettingsOpen(false); void lockNow() }}>{tr('Заблокировать сейчас')}</button>
        </div>
      )}
      <span className="hint warn-note">{tr('PIN не шифрует файлы. Заметки, записи и база лежат на диске как обычно, поэтому от чужих глаз на выключенном или украденном компьютере защищает шифрование диска: включите FileVault в «Системных настройках → Конфиденциальность и безопасность». PIN нужен, когда вы отошли от открытого компьютера. Блокировка закрывает интерфейс и API кабинета; у MCP и командной строки будут свои ключи.')}</span>
      <span className="hint">{tr('Забыли PIN: запустите «Сбросить PIN.command» из папки программы, файлы и записи при этом не меняются.')}</span>
      {error && <div className="error-box" style={{ marginTop: 0 }}>{error}</div>}
      <div className="switch-row">
        <span className="hint">{tr('Журнал действий: что и когда менялось, кем (кабинет, система, MCP, командная строка) и чем кончилось. Тексты записей в него не попадают.')}</span>
        <button className="btn s28" onClick={() => setAuditOpen(true)}>{tr('Открыть журнал')}</button>
      </div>
    </div>
  )
}

const KINDS: [string, string][] = [
  ['', tr('Все действия')], ['record.', tr('Записи')], ['task.', tr('Задачи')], ['decision.', tr('Решения')], ['attachment.', tr('Вложения')],
  ['person.', tr('Люди')], ['project.', tr('Проекты')], ['voice.', tr('Голоса')], ['settings', tr('Настройки')], ['lock.', tr('Блокировка')],
  ['backup.', tr('Копии базы')], ['storage.', tr('Хранилище')], ['job.', tr('Обработка')],
]
const PAGE = 100

function stamp(ts: string): string {
  const d = new Date(ts)
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString(localeTag(), { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function AuditModal() {
  const { auditOpen, setAuditOpen } = useApp()
  const [data, setData] = useState<AuditPage | null>(null)
  const [error, setError] = useState('')
  const [actor, setActor] = useState('')
  const [result, setResult] = useState('')
  const [kind, setKind] = useState('')
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(0)

  const query = useCallback((extra: Record<string, string> = {}) => {
    const params = new URLSearchParams({ actor, result, kind, since, until, q, ...extra })
    for (const [key, value] of [...params]) if (!value) params.delete(key)
    return params.toString()
  }, [actor, result, kind, since, until, q])

  useEffect(() => {
    if (!auditOpen) return
    let alive = true
    const timer = window.setTimeout(() => {
      api.get<AuditPage>('/api/audit?' + query({ limit: String(PAGE), offset: String(page * PAGE) }))
        .then(r => { if (alive) { setData(r); setError('') } })
        .catch(e => { if (alive) setError(errorText(e)) })
    }, 200)
    return () => { alive = false; window.clearTimeout(timer) }
  }, [auditOpen, query, page])
  useEffect(() => { setPage(0) }, [actor, result, kind, since, until, q])

  if (!auditOpen) return null
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE)) : 1
  const download = async () => {
    try {
      const response = await fetch('/api/audit/export?' + query(), { headers: { 'X-Memory-Desk': '1' } })
      if (!response.ok) throw new ApiError(response.status, response.statusText)
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a')
      link.href = url; link.download = tr('журнал-{0}.csv', [new Date().toISOString().slice(0, 10)])
      document.body.appendChild(link); link.click(); link.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 1500)
    } catch (e) { setError(errorText(e)) }
  }

  return (
    <div className="modal-wrap" onClick={e => { if (e.target === e.currentTarget) setAuditOpen(false) }}>
      <div className="modal audit" role="dialog" aria-label={tr('Журнал действий')}>
        <div>
          <div className="h3">{tr('Журнал действий')}</div>
          <div className="subtitle">{tr('Только дописывается; тексты записей в нём не хранятся, только названия действий и идентификаторы.')}</div>
        </div>
        {data && (
          <div className={'audit-chain ' + (data.chain.ok ? 'ok' : 'bad')} role="status">{tr('{0} Целостность: {1}{2}', [data.chain.ok ? '✓' : '⚠', data.chain.message, data.chain.count ? tr(' · строк: {0}', [data.chain.count]) : ''])}</div>
        )}
        <div className="audit-filters">
          <select value={actor} onChange={e => setActor(e.target.value)} aria-label={tr('Кто')}>
            <option value="">{tr('Все источники')}</option>
            {Object.entries(data?.actors ?? {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <select value={result} onChange={e => setResult(e.target.value)} aria-label={tr('Результат')}>
            <option value="">{tr('Любой результат')}</option>
            {Object.entries(data?.results ?? {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <select value={kind} onChange={e => setKind(e.target.value)} aria-label={tr('Что')}>
            {KINDS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <input type="date" value={since} onChange={e => setSince(e.target.value)} aria-label={tr('С даты')} />
          <input type="date" value={until} onChange={e => setUntil(e.target.value)} aria-label={tr('По дату')} />
          <input type="text" className="audit-q" placeholder={tr('Поиск по действию')} value={q} onChange={e => setQ(e.target.value)} aria-label={tr('Поиск')} />
        </div>
        <div className="audit-list">
          {data && data.items.length === 0 && <div className="empty">{tr('Подходящих строк нет')}</div>}
          {data?.items.map(r => (
            <div className={'audit-row ' + r.result} key={r.n}>
              <span className="when num">{stamp(r.ts)}</span>
              <span className="who">{r.actorLabel}</span>
              <span className="what">{r.label}{r.object && <span className="obj"> · {r.object}</span>}{r.detail && <span className="det"> ({r.detail})</span>}</span>
              <span className={'res ' + r.result}>{data.results[r.result] ?? r.result}</span>
            </div>
          ))}
          {!data && !error && <div className="empty">{tr('Загрузка…')}</div>}
        </div>
        {error && <div className="error-box" style={{ marginTop: 0 }}>{error}</div>}
        <div className="modal-foot audit-foot">
          <span className="hint">{data ? tr('Найдено: {0}', [data.total]) : ''}</span>
          <span className="audit-pager">
            <button className="btn s28" disabled={page === 0} onClick={() => setPage(p => p - 1)}>←</button>
            <span className="num">{page + 1} / {pages}</span>
            <button className="btn s28" disabled={page + 1 >= pages} onClick={() => setPage(p => p + 1)}>→</button>
          </span>
          <button className="btn s34" onClick={() => void download()}>{tr('Скачать CSV')}</button>
          <button className="btn s34" onClick={() => setAuditOpen(false)}>{tr('Закрыть')}</button>
        </div>
      </div>
    </div>
  )
}
