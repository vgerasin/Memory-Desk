import { useEffect, useRef, useState } from 'react'
import { api, errorText } from '../api'
import { useApp } from '../store'
import { useApi } from '../useApi'
import type { MeetingDetail, RebuildState, TemplatesResponse } from '../types'
import { tr } from '../i18n'

/** «Шаблон заметки» в карточке записи: какой шаблон применён и кнопка «Пересобрать по другому шаблону». */
export function RebuildPanel({ meeting }: { meeting: MeetingDetail }) {
  const { showToast, invalidate } = useApp()
  const { data } = useApi<TemplatesResponse>('/api/templates')
  const [open, setOpen] = useState(false)
  const [choice, setChoice] = useState('')
  const [state, setState] = useState<RebuildState>({ state: 'idle' })
  const [error, setError] = useState('')
  const timer = useRef<number | null>(null)
  const current = meeting.template
  const names = Object.fromEntries((data?.items ?? []).map(t => [t.id, t.name]))

  const stop = () => { if (timer.current != null) { window.clearInterval(timer.current); timer.current = null } }
  const poll = () => {
    stop()
    timer.current = window.setInterval(async () => {
      try {
        const r = await api.get<RebuildState>(`/api/meetings/${meeting.id}/rebuild`)
        setState(r)
        if (r.state === 'done' || r.state === 'error') {
          stop()
          if (r.state === 'done') {
            invalidate()
            const kept = (r.keptTasks ?? 0) + (r.keptDecisions ?? 0)
            showToast(r.fallback
              ? tr('Шаблон «{0}» не получился, заметка составлена стандартным', [names[r.requestedId ?? ''] ?? r.requestedId])
              : tr('Заметка пересобрана по шаблону «{0}»', [names[r.templateId ?? ''] ?? r.templateId]) + (kept ? tr(' · сохранено подтверждённого: {0}', [kept]) : ''))
          }
        }
      } catch (e) { stop(); setError(errorText(e)) }
    }, 1500)
  }
  useEffect(() => stop, [])
  useEffect(() => { setOpen(false); setState({ state: 'idle' }); setError(''); stop() }, [meeting.id])
  useEffect(() => { if (open && !choice) setChoice(current?.suggestedId || 'meeting') }, [open, choice, current])

  if (!data) return null
  const run = async () => {
    setError('')
    try {
      setState(await api.post<RebuildState>(`/api/meetings/${meeting.id}/rebuild`, { templateId: choice }))
      poll()
    } catch (e) { setError(errorText(e)) }
  }
  const running = state.state === 'running'
  const label = current?.id ? (names[current.id] ?? current.name) : tr('стандартный')

  return (
    <section className="rebuild" aria-label={tr('Шаблон заметки')}>
      <div className="rebuild-head">
        <span>{tr('Шаблон заметки: ')}{' '}<b>{label}</b></span>
        {!open && <button className="link-btn" onClick={() => setOpen(true)}>{tr('Пересобрать по другому')}</button>}
      </div>
      {open && (
        <div className="rebuild-body">
          <div className="backup-plan">
            <label htmlFor="rebuild-template">{tr('Шаблон')}</label>
            <select id="rebuild-template" value={choice} disabled={running} onChange={e => setChoice(e.target.value)}>
              {data.items.map(t => <option key={t.id} value={t.id}>{t.name}{t.id === current?.suggestedId ? tr(' · подходит типу записи') : ''}</option>)}
            </select>
            <button className="btn s28 accent" disabled={running || !choice} onClick={() => void run()}>{running ? tr('Составляю…') : tr('Пересобрать')}</button>
            <button className="btn s28" disabled={running} onClick={() => { setOpen(false); setError('') }}>{tr('Закрыть')}</button>
          </div>
          <span className="hint">{tr('Разделы заметки составляются заново по стенограмме (около минуты). Подтверждённые и закрытые задачи и решения сохраняются, черновики заменяются новыми. Ручные правки в разделах заметки пропадут; прежняя заметка остаётся в копиях.')}</span>
          {state.state === 'done' && state.fallback && <span className="hint bad">{tr('Шаблон не получился: {0}. Заметка составлена стандартным.', [state.fallback])}</span>}
          {(error || state.state === 'error') && <span className="hint bad">{error || state.message}</span>}
        </div>
      )}
    </section>
  )
}
