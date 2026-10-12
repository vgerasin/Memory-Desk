import { useCallback, useState, type ReactNode } from 'react'
import { api, errorText } from '../api'
import { useApp } from '../store'

export interface BulkResult { updated: number; noteMissing?: number; failed: { id: string; error: string }[]; failedCount: number }

export const isoDay = (offset: number) => {
  const d = new Date()
  d.setDate(d.getDate() + offset)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** Режим выбора: набор отмеченных элементов и запуск массового действия с итогом в уведомлении. */
export function useBulk() {
  const { invalidate, showToast } = useApp()
  const [on, setOn] = useState(false)
  const [ids, setIds] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)

  const toggle = useCallback((id: string) => setIds(prev => {
    const next = new Set(prev)
    if (next.has(id)) next.delete(id); else next.add(id)
    return next
  }), [])
  const setAll = useCallback((all: string[]) => setIds(new Set(all)), [])
  const clear = useCallback(() => setIds(new Set()), [])
  const stop = useCallback(() => { setOn(false); setIds(new Set()) }, [])

  const run = useCallback(async (path: string, body: Record<string, unknown>, ok: string): Promise<boolean> => {
    if (busy || ids.size === 0) return false
    setBusy(true)
    try {
      const result = await api.post<BulkResult>(path, { ids: [...ids], ...body })
      invalidate()
      const parts = [`${ok}: ${result.updated}`]
      if (result.failedCount) parts.push(`не удалось: ${result.failedCount}${result.failed[0] ? ` (${result.failed[0].error})` : ''}`)
      if (result.noteMissing) parts.push(`в заметках строку не нашли: ${result.noteMissing}`)
      showToast(parts.join(' · '))
      setIds(new Set())
      return true
    } catch (e) {
      showToast(errorText(e))
      return false
    } finally { setBusy(false) }
  }, [busy, ids, invalidate, showToast])

  return { on, setOn, ids, toggle, setAll, clear, stop, run, busy }
}

/** Панель действий над выбранным; закреплена внизу окна. */
export function BulkBar({ count, total, onAll, onClear, onStop, children }: {
  count: number; total: number; onAll: () => void; onClear: () => void; onStop: () => void; children: ReactNode
}) {
  return (
    <div className="bulk-bar" role="toolbar" aria-label="Действия над выбранным">
      <div className="bulk-count">Выбрано: <b>{count}</b></div>
      <button className="btn link s28" onClick={count >= total ? onClear : onAll}>{count >= total ? 'Снять всё' : `Выбрать все (${total})`}</button>
      <div className="bulk-actions">{children}</div>
      <button className="btn s28 muted" onClick={onStop}>Готово</button>
    </div>
  )
}

/** Срок пачкой: быстрые кнопки, дата и «Без срока». */
export function BulkDue({ disabled, onPick }: { disabled: boolean; onPick: (iso: string) => void }) {
  const [open, setOpen] = useState(false)
  const quick: [string, string][] = [['Сегодня', isoDay(0)], ['Завтра', isoDay(1)], ['Через неделю', isoDay(7)]]
  return (
    <span className="bulk-pop-wrap">
      <button className="btn s28" disabled={disabled} onClick={() => setOpen(o => !o)}>Срок…</button>
      {open && (
        <div className="bulk-pop" onKeyDown={e => { if (e.key === 'Escape') setOpen(false) }}>
          {quick.map(([label, iso]) => <button key={label} className="btn s28" onClick={() => { setOpen(false); onPick(iso) }}>{label}</button>)}
          <input type="date" aria-label="Дата срока" onChange={e => { if (e.target.value) { setOpen(false); onPick(e.target.value) } }} />
          <button className="btn link s28" onClick={() => { setOpen(false); onPick('') }}>Без срока</button>
        </div>
      )}
    </span>
  )
}

/** Ответственный пачкой. */
export function BulkOwner({ disabled, onPick }: { disabled: boolean; onPick: (owner: string) => void }) {
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState('')
  const apply = () => { const v = value.trim(); if (!v) return; setOpen(false); setValue(''); onPick(v) }
  return (
    <span className="bulk-pop-wrap">
      <button className="btn s28" disabled={disabled} onClick={() => setOpen(o => !o)}>Ответственный…</button>
      {open && (
        <div className="bulk-pop" onKeyDown={e => { if (e.key === 'Enter') apply(); if (e.key === 'Escape') setOpen(false) }}>
          <input autoFocus value={value} maxLength={120} placeholder="Имя ответственного" onChange={e => setValue(e.target.value)} aria-label="Ответственный" />
          <button className="btn accent s28" disabled={!value.trim()} onClick={apply}>Назначить</button>
        </div>
      )}
    </span>
  )
}

/** Подпись «Показать ещё» под списком, который отдаётся страницами. */
export function MoreButton({ shown, total, onMore }: { shown: number; total: number; onMore: () => void }) {
  if (shown >= total) return null
  return (
    <div className="more-row">
      <button className="btn s32" onClick={onMore}>Показать ещё</button>
      <span className="muted-text">показано {shown} из {total}</span>
    </div>
  )
}

export const PAGE = 50
