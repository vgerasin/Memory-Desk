import { useEffect, useState } from 'react'
import { api } from '../api'
import { useActions } from '../actions'
import { clockOf } from '../format'
import type { CalendarSuggestion } from '../types'
import { tr } from '../i18n'

/** Подсказка над записью: «похоже на событие в Календаре» — применить участников, проект и название. */
export function CalendarSuggest({ meetingId, enabled, version }: { meetingId: string; enabled: boolean; version: number }) {
  const actions = useActions()
  const [data, setData] = useState<CalendarSuggestion | null>(null)

  useEffect(() => {
    if (!enabled) { setData(null); return }
    let alive = true
    api.get<{ suggestion: CalendarSuggestion | null }>(`/api/meetings/${meetingId}/calendar`)
      .then(r => { if (alive) setData(r.suggestion) }).catch(() => { if (alive) setData(null) })
    return () => { alive = false }
  }, [meetingId, enabled, version])

  if (!data || data.linked || !data.event) return null
  const e = data.event
  const act = (body: { action: string; useTitle?: boolean }, ok: string) =>
    actions.safe(async () => { await api.post(`/api/meetings/${meetingId}/calendar`, body); setData(null) }, ok)
  const parts = [
    data.people?.length ? tr('участники: ') + data.people.join(', ') : '',
    data.project ? tr('проект: ') + data.project.title : '',
  ].filter(Boolean)

  return (
    <div className="cal-suggest">
      <div className="cal-text">
        <b>{tr('Похоже на событие в Календаре: «{0}»', [e.title])}</b>
        <span>{clockOf(e.start)}–{clockOf(e.end)}{e.calendar ? ' · ' + e.calendar : ''}{parts.length ? ' · ' + parts.join(' · ') : ''}</span>
      </div>
      <div className="cal-actions">
        <button className="btn s28 muted" onClick={() => act({ action: 'dismiss' }, tr('Подсказка скрыта'))}>{tr('Не то')}</button>
        {data.titleDiffers && <button className="btn s28" onClick={() => act({ action: 'apply', useTitle: true }, tr('Применено вместе с названием'))}>{tr('С названием')}</button>}
        <button className="btn s28 accent" onClick={() => act({ action: 'apply' }, tr('Данные из Календаря применены'))}>{tr('Применить')}</button>
      </div>
    </div>
  )
}
