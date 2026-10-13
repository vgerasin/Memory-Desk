import { navigate, recordLink } from '../router'
import { dayLabel, ownerLabel } from '../format'
import { useApi } from '../useApi'
import type { MorningMeeting, MorningPayload, MorningTask } from '../types'

function dueText(task: MorningTask): string {
  return task.due ? dayLabel(task.due) : task.dueText
}

function TaskList({ title, tone, items, total }: { title: string; tone?: 'bad'; items: MorningTask[]; total: number }) {
  if (total === 0) return null
  return (
    <div className="morning-col">
      <div className={'label-caps' + (tone ? ' ' + tone : '')}>{title} · {total}</div>
      {items.map(t => (
        <button key={t.id} className="hero-item" onClick={() => navigate(recordLink(t.meetingId, { t: t.tSec }))}>
          <div className="t">{t.text}</div>
          <div className="m">{[ownerLabel(t.owner), dueText(t)].filter(Boolean).join(' · ')}</div>
        </button>
      ))}
      {total > items.length && <button className="link-btn" onClick={() => navigate('/tasks')}>Ещё {total - items.length}</button>}
    </div>
  )
}

function Meetings({ items }: { items: MorningMeeting[] }) {
  if (items.length === 0) return null
  return (
    <div className="morning-col wide">
      <div className="label-caps">Встречи сегодня · {items.length}</div>
      {items.map(m => (
        <div className="hero-item static" key={m.start + m.title}>
          <div className="t">{m.start.slice(11, 16)} · {m.title}</div>
          {m.headline && <div className="m">{m.headline}</div>}
        </div>
      ))}
    </div>
  )
}

/** Утренняя сводка на «Сегодня»: просрочено, встречи с «главным к встрече», задачи на неделю, зависшие. Пустая — не показывается. */
export function MorningCard() {
  const { data } = useApi<MorningPayload>('/api/morning')
  if (!data || (data.empty && data.counts.stuck === 0)) return null
  const c = data.counts
  return (
    <section className="card morning" aria-label="Утренняя сводка">
      <div className="panel-head">
        <h3 className="h3">Утро</h3>
        <span className="muted-text" style={{ fontSize: 13 }}>{data.message}</span>
      </div>
      <div className="morning-cols">
        <Meetings items={data.meetings} />
        <TaskList title="Просрочено" tone="bad" items={data.overdue} total={c.overdue} />
        <TaskList title="Ваши на неделе" items={data.mine} total={c.mine} />
        <TaskList title="Вам обещали на неделе" items={data.promised} total={c.promised} />
      </div>
      {c.stuck > 0 && (
        <div className="morning-foot">
          <span>Зависших задач: <b>{c.stuck}</b> — без движения больше двух недель.</span>
          <button className="btn s28" onClick={() => navigate('/tasks?stuck=1')}>Разобрать</button>
        </div>
      )}
    </section>
  )
}
