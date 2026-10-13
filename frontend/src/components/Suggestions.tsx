import { useState } from 'react'
import { api, errorText } from '../api'
import { navigate } from '../router'
import { useApp } from '../store'
import { useApi } from '../useApi'
import type { PendingSuggestionsDto, SuggestionDto, SuggestionsDto } from '../types'

const KIND_DOT: Record<SuggestionDto['kind'], string> = { event: '#0A84FF', mail: '#BF5AF2', reminder: '#FF9F0A' }
const CREATE_LABEL: Record<SuggestionDto['kind'], string> = { event: 'В Календарь', mail: 'Черновик письма', reminder: 'В «Напоминания»' }
const tc = (sec: number) => `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(Math.floor(sec % 60)).padStart(2, '0')}`

function when(s: SuggestionDto): string {
  if (s.kind === 'mail') return s.who ? `Кому: ${s.who}` : 'Адресат не назван'
  const parts = [s.dayLabel || (s.kind === 'event' ? 'Укажите дату' : 'Без срока')]
  if (s.kind === 'event') parts.push(s.allDay ? 'весь день' : s.clock || 'укажите время')
  return (s.kind === 'reminder' && s.deadline && s.dayLabel ? 'до ' : '') + parts.join(' · ')
}

function Card({ item, onJump }: { item: SuggestionDto; onJump: (sec: number) => void }) {
  const { showToast, invalidate } = useApp()
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState({ title: item.title, day: item.day, clock: item.clock, allDay: item.allDay, who: item.who, notes: item.notes })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const base = `/api/suggestions/${item.id}`
  const run = async (fn: () => Promise<SuggestionDto>, done?: (r: SuggestionDto) => string) => {
    setBusy(true); setError('')
    try { const r = await fn(); if (done) showToast(done(r)); setEditing(false); invalidate() } catch (e) { setError(errorText(e)) }
    setBusy(false)
  }
  const startEdit = () => { setForm({ title: item.title, day: item.day, clock: item.clock, allDay: item.allDay, who: item.who, notes: item.notes }); setEditing(true) }

  if (item.status === 'dismissed') {
    return (
      <div className="sg dismissed">
        <span className="sg-title">{item.title}</span>
        <span className="muted-text">отклонено</span>
        <button className="link-btn" disabled={busy} onClick={() => void run(() => api.post(`${base}/restore`))}>Вернуть</button>
      </div>
    )
  }
  return (
    <div className={'sg' + (item.status === 'created' ? ' done' : '')}>
      <div className="sg-head">
        <span className="sg-kind"><i style={{ background: KIND_DOT[item.kind] }} />{item.kindLabel}</span>
        {item.engine === 'rules' && <span className="sg-note" title="Модель была недоступна: найдено по простым правилам">по правилам</span>}
        {item.status === 'created' && <span className="sg-ok">✓ Создано</span>}
      </div>
      {editing ? (
        <div className="sg-form">
          <input value={form.title} maxLength={200} onChange={e => setForm({ ...form, title: e.target.value })} aria-label="Название" />
          {item.kind !== 'mail' && (
            <div className="sg-row">
              <input type="date" value={form.day} onChange={e => setForm({ ...form, day: e.target.value })} aria-label="Дата" />
              {item.kind === 'event' && !form.allDay && <input type="time" value={form.clock} onChange={e => setForm({ ...form, clock: e.target.value })} aria-label="Время" />}
              {item.kind === 'event' && (
                <label className="sg-check"><input type="checkbox" checked={form.allDay} onChange={e => setForm({ ...form, allDay: e.target.checked })} />весь день</label>
              )}
            </div>
          )}
          {item.kind === 'mail' && <input value={form.who} maxLength={200} placeholder="Кому (имя участника)" onChange={e => setForm({ ...form, who: e.target.value })} aria-label="Кому" />}
          <textarea value={form.notes} maxLength={1000} placeholder="Заметка (необязательно)" onChange={e => setForm({ ...form, notes: e.target.value })} aria-label="Заметка" />
          <div className="sg-actions">
            <button className="btn s28 muted" onClick={() => setEditing(false)}>Отмена</button>
            <button className="btn s28 accent" disabled={busy || !form.title.trim()} onClick={() => void run(() => api.patch(base, form))}>Сохранить</button>
          </div>
        </div>
      ) : (
        <>
          <div className="sg-title">{item.title}</div>
          <div className={'sg-when' + (item.needs.length || item.past ? ' warn' : '')}>
            {when(item)}{item.past && item.status === 'new' && item.kind !== 'mail' ? ' · дата уже прошла' : ''}
          </div>
          <div className="sg-quote">
            «{item.quote}» <button className="link-btn mono" onClick={() => onJump(item.sec)} title="Перейти к месту в записи">{item.t || tc(item.sec)}</button>
          </div>
          {item.status === 'created' && item.createdRef && <div className="muted-text">{item.createdRef}</div>}
          {item.status === 'new' && (
            <>
              {item.kind === 'mail' && <div className="muted-text">Откроется черновик в «Почте»: письмо не отправляется, отправляете вы сами.</div>}
              <div className="sg-actions">
                <button className="btn s28 muted" disabled={busy} onClick={() => void run(() => api.post(`${base}/dismiss`))}>Отклонить</button>
                <button className="btn s28" disabled={busy} onClick={startEdit}>Изменить</button>
                <button className="btn s28 accent" disabled={busy || !item.canCreate} onClick={() => void run(() => api.post(base + '/create'), r => r.message ?? 'Готово')}
                  title={item.canCreate ? '' : 'Сначала нажмите «Изменить» и укажите недостающее'}>{CREATE_LABEL[item.kind]}</button>
              </div>
            </>
          )}
        </>
      )}
      {error && <div className="error-box" style={{ marginTop: 6 }}>{error}</div>}
    </div>
  )
}

/** Предложения по записи: ничего не создаётся, пока вы не нажмёте кнопку на карточке. */
export function SuggestionsPanel({ meetingId, onJump }: { meetingId: string; onJump: (sec: number) => void }) {
  const { invalidate, showToast } = useApp()
  const { data } = useApi<SuggestionsDto>(`/api/meetings/${meetingId}/suggestions`)
  const [busy, setBusy] = useState(false)
  if (!data) return null
  const running = data.scan.state === 'running' || busy
  const fresh = data.items.filter(i => i.status === 'new').length
  const find = async () => {
    setBusy(true)
    try { await api.post(`/api/meetings/${meetingId}/suggestions/scan`); invalidate() } catch (e) { showToast(errorText(e)) }
    setBusy(false)
  }
  return (
    <section className="sg-panel" aria-label="Предложения после встречи">
      <div className="sg-panel-head">
        <div className="label-caps">Предложения после встречи{fresh ? ` · ${fresh}` : ''}</div>
        <button className="link-btn" onClick={() => void find()} disabled={running}>{running ? 'Ищу договорённости…' : 'Найти заново'}</button>
      </div>
      {data.items.length === 0 && !running && (
        <div className="muted-text">
          {data.scan.state === 'error' ? `Поиск не удался: ${data.scan.message}` : 'Договорённостей для Календаря, писем и напоминаний не найдено. Ничего не создаётся без вашего нажатия.'}
        </div>
      )}
      {data.items.map(item => <Card key={item.id + item.status + item.title + item.day + item.clock} item={item} onJump={onJump} />)}
    </section>
  )
}

/** «Сегодня»: новые предложения по свежим записям. */
export function PendingSuggestions() {
  const { data } = useApi<PendingSuggestionsDto>('/api/suggestions')
  if (!data || data.total === 0) return null
  return (
    <section className="card panel" aria-label="Предложения после встреч">
      <div className="panel-head"><div className="h3">Предложения после встреч</div><span className="muted-text">{data.total}</span></div>
      {data.items.slice(0, 4).map(i => (
        <button key={i.id} className="sg-line" onClick={() => navigate(`/records/${i.meetingId}`)}>
          <span className="sg-kind"><i style={{ background: KIND_DOT[i.kind] }} />{i.kindLabel}</span>
          <span className="sg-title">{i.title}</span>
          <span className="muted-text">{i.meetingTitle}</span>
        </button>
      ))}
    </section>
  )
}
