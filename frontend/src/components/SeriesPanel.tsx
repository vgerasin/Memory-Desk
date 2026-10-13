import { useState } from 'react'
import { navigate, recordLink } from '../router'
import { dayLabel } from '../format'
import { useApi } from '../useApi'
import type { SeriesInfo } from '../types'
import { tr } from '../i18n'

function List({ title, items, tone }: { title: string; items: string[]; tone?: 'warn' }) {
  if (items.length === 0) return null
  return (
    <div className="series-block">
      <div className={'label-caps' + (tone ? ' ' + tone : '')}>{title}</div>
      <ul>{items.map((t, i) => <li key={i}>{t}</li>)}</ul>
    </div>
  )
}

const ordinal = (n: number) => tr('{0}-я', [n])

/** Панель «Серия встреч» в карточке записи: что изменилось с прошлого раза и какие вопросы висят подряд. Нет серии — нет панели. */
export function SeriesPanel({ meetingId }: { meetingId: string }) {
  const { data } = useApi<{ series: SeriesInfo | null }>(`/api/meetings/${meetingId}/series`)
  const [open, setOpen] = useState(false)
  const s = data?.series
  if (!s) return null
  const repeated = s.questionsRepeated
  const changes = s.previous
    ? s.decisionsNew.length + s.tasksNew.length + s.tasksClosed.length + s.questionsNew.length + s.questionsResolved.length + s.tasksOpen.length
    : 0
  return (
    <section className="series" aria-label={tr('Серия встреч')}>
      <button className="series-head" onClick={() => setOpen(o => !o)} aria-expanded={open}>
        <span className="series-title">{tr('Серия «{0}» · {1} встреча из {2}', [s.title, ordinal(s.position), s.count])}</span>
        {repeated.length > 0 && <span className="stuck-badge">{tr('вопрос висит {0} встречу подряд', [ordinal(repeated[0].count)])}</span>}
        <span className="series-toggle">{open ? tr('Свернуть') : s.previous ? tr('Что изменилось') : tr('Все встречи')}</span>
      </button>
      {open && (
        <div className="series-body">
          <div className="series-line">
            {s.meetings.map(m => (
              <button key={m.id} className={'chip' + (m.current ? ' on' : '')} disabled={m.current} onClick={() => navigate(recordLink(m.id))}>
                {dayLabel(m.date)}
              </button>
            ))}
          </div>
          {repeated.map(q => (
            <div className="series-alert" key={q.text}>{tr('«{0}» — вопрос открыт уже {1} встречи подряд, с {2}. Решите его или снимите.', [q.text, q.count, dayLabel(q.since)])}</div>
          ))}
          {s.previous && changes > 0 && (
            <>
              <div className="muted-text" style={{ fontSize: 13 }}>{tr('С прошлого раза ({0}):', [dayLabel(s.previous.date)])}</div>
              <List title={tr('Новые решения')} items={s.decisionsNew.map(d => d.text)} />
              <List title={tr('Закрыто из прошлых задач')} items={s.tasksClosed.map(t => t.text)} />
              <List title={tr('Всё ещё открыто с прошлого раза')} tone="warn" items={s.tasksOpen.map(t => t.text + (t.owner ? ` (${t.owner})` : ''))} />
              <List title={tr('Новые вопросы')} items={s.questionsNew.map(q => q.text)} />
              <List title={tr('Больше не обсуждали')} items={s.questionsResolved.map(q => q.text)} />
            </>
          )}
          {s.previous && changes === 0 && <div className="muted-text">{tr('С прошлой встречи ничего не изменилось.')}</div>}
        </div>
      )}
    </section>
  )
}
