import { useState } from 'react'
import { api, errorText } from '../api'
import { useApp } from '../store'
import { tr } from '../i18n'

export interface EventDraft { title: string; date: string; time?: string; allDay?: boolean; meetingId?: string }

const pad = (v: number) => String(v).padStart(2, '0')

/** Небольшое окно «Добавить в Календарь»: название, день, время, длительность. */
export function EventDialog({ draft, onClose }: { draft: EventDraft; onClose: () => void }) {
  const { showToast } = useApp()
  const [title, setTitle] = useState(draft.title)
  const [date, setDate] = useState(draft.date)
  const [time, setTime] = useState(draft.time ?? '10:00')
  const [allDay, setAllDay] = useState(!!draft.allDay)
  const [minutes, setMinutes] = useState(60)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const save = async () => {
    if (!title.trim() || !date || busy) return
    setBusy(true); setError('')
    try {
      const start = new Date(`${date}T${allDay ? '00:00' : time}:00`)
      const end = new Date(start.getTime() + minutes * 60000)
      const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:00`
      const r = await api.post<{ message: string }>('/api/calendar/events', {
        title: title.trim(), start: iso(start), end: allDay ? null : iso(end), allDay, meetingId: draft.meetingId ?? null,
      })
      showToast(r.message)
      onClose()
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }

  return (
    <div className="modal-wrap" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal small" role="dialog" aria-label={tr('Добавить в Календарь')}
        onKeyDown={e => { if (e.key === 'Escape') onClose(); if (e.key === 'Enter' && (e.target as HTMLElement).tagName === 'INPUT') void save() }}>
        <div className="h3">{tr('Добавить в Календарь')}</div>
        <div className="field">
          <label htmlFor="ev-title">{tr('Название')}</label>
          <input id="ev-title" type="text" autoFocus value={title} onChange={e => setTitle(e.target.value)} />
        </div>
        <div className="item-editor-row">
          <label><span>{tr('День')}</span><input type="date" value={date} onChange={e => setDate(e.target.value)} /></label>
          {!allDay && <label><span>{tr('Начало')}</span><input type="time" value={time} onChange={e => setTime(e.target.value)} style={{ minWidth: 110 }} /></label>}
          {!allDay && (
            <label><span>{tr('Длительность')}</span>
              <select value={minutes} onChange={e => setMinutes(Number(e.target.value))}>
                {[15, 30, 45, 60, 90, 120].map(m => <option key={m} value={m}>{tr('{0} мин', [m])}</option>)}
              </select>
            </label>
          )}
        </div>
        <div className="switch-row">
          <label style={{ fontSize: 14 }}>{tr('Весь день')}</label>
          <button className={'switch' + (allDay ? ' on' : '')} role="switch" aria-checked={allDay} onClick={() => setAllDay(v => !v)}><i /></button>
        </div>
        {error && <div className="error-box" style={{ marginTop: 0 }}>{error}</div>}
        <div className="modal-foot">
          <button className="btn s34" onClick={onClose}>{tr('Отмена')}</button>
          <button className="btn accent s34" onClick={() => void save()} disabled={!title.trim() || !date || busy}>{tr('Добавить')}</button>
        </div>
      </div>
    </div>
  )
}
