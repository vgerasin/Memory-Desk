import type { ReactNode } from 'react'
import { navigate, recordLink } from '../router'
import { dayLabel, due, plural, tc } from '../format'
import type { DecisionDto, FactLine, MeetingBrief, TaskDto } from '../types'

export function BackLink({ to, label }: { to: string; label: string }) {
  return <button className="back-link" onClick={() => navigate(to)}>← {label}</button>
}

export function Tile({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: string; tone?: 'bad' | 'warn' }) {
  return (
    <div className="card r20 dos-tile">
      <span className="label-caps">{label}</span>
      <span className={'dos-tile-value' + (tone ? ' ' + tone : '')}>{value}</span>
      {sub && <span className="dos-tile-sub">{sub}</span>}
    </div>
  )
}

export function Section({ title, count, children, wide }: { title: string; count?: number | null; children: ReactNode; wide?: boolean }) {
  return (
    <section className={'card r20 dos-section' + (wide ? ' wide' : '')}>
      <div className="dos-section-head">
        <h3 className="h3">{title}</h3>
        {count ? <span className="dos-count">{count}</span> : null}
      </div>
      {children}
    </section>
  )
}

export function Empty({ text }: { text: string }) {
  return <div className="muted-text" style={{ fontSize: 14 }}>{text}</div>
}

export function TaskLine({ task, showOwner }: { task: TaskDto; showOwner?: boolean }) {
  const d = due(task.due, task.dueText)
  return (
    <button className="dos-line" onClick={() => navigate(recordLink(task.meetingId, { view: 'actions', t: task.tSec }))}>
      <span className="dos-line-text">{task.text}{task.draft && <span className="chip-draft">черновик</span>}</span>
      <span className="dos-line-meta">
        {showOwner && task.owner ? task.owner + ' · ' : ''}<span style={{ color: d.color }}>{d.label}</span>
        {task.stuck ? ` · без движения ${task.ageDays} дн.` : ''} · {task.meetingTitle}
      </span>
    </button>
  )
}

export function DecisionLine({ item }: { item: DecisionDto }) {
  return (
    <button className="dos-line" onClick={() => navigate(recordLink(item.meetingId, { view: 'actions', t: item.tSec }))}>
      <span className="dos-line-text">{item.text}{item.draft && <span className="chip-draft">черновик</span>}</span>
      <span className="dos-line-meta">{dayLabel(item.meetingDate)} · {item.meetingTitle}</span>
    </button>
  )
}

export function FactRow({ fact }: { fact: FactLine }) {
  return (
    <button className="dos-line" onClick={() => navigate(recordLink(fact.meetingId, { view: 'questions', t: fact.sec }))}>
      <span className="dos-line-text">{fact.text}</span>
      <span className="dos-line-meta">{dayLabel(fact.date)} · {fact.meetingTitle}{fact.sec != null ? ' · ' + tc(fact.sec) : ''}</span>
    </button>
  )
}

export function MeetingRow({ m }: { m: MeetingBrief }) {
  return (
    <button className="dos-line" onClick={() => navigate(recordLink(m.id))}>
      <span className="dos-line-text">{m.title}</span>
      <span className="dos-line-meta">{dayLabel(m.date)}{m.time ? ', ' + m.time : ''}{m.project ? ' · ' + m.project.title : ''}</span>
    </button>
  )
}

export function agoLabel(days: number | null): string {
  if (days == null) return '—'
  if (days === 0) return 'сегодня'
  if (days === 1) return 'вчера'
  return plural(days, 'день', 'дня', 'дней') + ' назад'
}
