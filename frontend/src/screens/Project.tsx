import { useEffect, useState } from 'react'
import { api, errorText } from '../api'
import { navigate, recordLink, useRoute } from '../router'
import { useApi } from '../useApi'
import { useApp } from '../store'
import { dayLabel, plural } from '../format'
import type { ProjectDossier, ProjectStatus } from '../types'
import { Avatar, ErrorBox, Loading } from '../components/ui'
import { agoLabel, BackLink, DecisionLine, Empty, FactRow, MeetingRow, Section, TaskLine, Tile } from '../components/Dossier'
import { DocMenu } from '../components/DocMenu'

const STATUSES: { key: ProjectStatus; label: string }[] = [
  { key: 'active', label: 'В работе' }, { key: 'paused', label: 'На паузе' }, { key: 'done', label: 'Завершён' },
]
const HEALTH = { ok: { label: 'Всё спокойно', color: '#34C759' }, warn: { label: 'Требует внимания', color: '#FF9F0A' }, bad: { label: 'Есть проблемы', color: '#FF3B30' } }

export function Project() {
  const { segments } = useRoute()
  const id = decodeURIComponent(segments[1] ?? '')
  const { data, error } = useApi<ProjectDossier>(id ? `/api/projects/${encodeURIComponent(id)}` : null)
  const { setPrep, setMaterial, showToast, invalidate } = useApp()
  const [goal, setGoal] = useState<string | null>(null)
  useEffect(() => { setGoal(null) }, [id])

  if (error && !data) return <><BackLink to="/projects" label="Проекты" /><ErrorBox text={error} /></>
  if (!data) return <><BackLink to="/projects" label="Проекты" /><Loading /></>
  const { project: p, health, counts } = data
  const patch = async (body: { status?: ProjectStatus; goal?: string }) => {
    try { await api.patch(`/api/projects/${encodeURIComponent(p.id)}`, body); invalidate() }
    catch (e) { showToast(errorText(e)) }
  }
  const h = HEALTH[health.level]
  const maxWeek = Math.max(1, ...data.weeks.map(w => w.meetings))
  return (
    <>
      <BackLink to="/projects" label="Проекты" />
      <div className="dos-head card">
        <span className="dos-dot" style={{ background: p.color }} />
        <div className="dos-head-main">
          <h1 className="h1" style={{ fontSize: 30 }}>{p.title}</h1>
          <div className="dos-sub">
            <select className="dos-status" value={p.status} aria-label="Статус проекта" onChange={e => void patch({ status: e.target.value as ProjectStatus })}>
              {STATUSES.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select>
            {counts.records > 0 && <span> · {plural(counts.records, 'запись', 'записи', 'записей')}{counts.first ? `, с ${dayLabel(counts.first).toLowerCase()}` : ''}</span>}
          </div>
        </div>
        <div className="dos-head-actions">
          <button className="btn s34" onClick={() => navigate('/records?project=' + encodeURIComponent(p.title))}>Записи</button>
          <button className="btn s34" onClick={() => setMaterial({ kind: 'deck', projectId: p.id, title: p.title })}>Презентация</button>
          <button className="btn s34" onClick={() => setMaterial({ kind: 'map', projectId: p.id, title: p.title })}>Карта</button>
          <DocMenu scope={{ projectId: p.id, title: p.title }} />
          <button className="btn s34" onClick={() => setMaterial({ kind: 'digest', projectId: p.id, title: p.title })}>Дайджест</button>
          <button className="btn accent s34" onClick={() => setPrep({ projectId: p.id, title: p.title })}>Подготовка к встрече</button>
        </div>
      </div>

      <div className="dos-goal card r20">
        <span className="label-caps">Цель</span>
        {goal === null
          ? <button className="dos-goal-text" onClick={() => setGoal(p.goal)}>{p.goal || <span style={{ color: 'var(--text3)' }}>Цель не указана — нажмите, чтобы написать</span>}</button>
          : <div className="dos-editor" style={{ padding: 0, boxShadow: 'none' }}>
              <textarea autoFocus value={goal} rows={3} maxLength={2000} onChange={e => setGoal(e.target.value)} aria-label="Цель проекта" />
              <div className="dos-editor-actions">
                <button className="btn accent s34" onClick={async () => { await patch({ goal }); setGoal(null) }}>Сохранить</button>
                <button className="btn s34" onClick={() => setGoal(null)}>Отмена</button>
                <span className="hint" style={{ marginLeft: 'auto' }}>Пишется в файл проекта в Obsidian</span>
              </div>
            </div>}
      </div>

      <div className="dos-health" style={{ borderColor: h.color }}>
        <i style={{ background: h.color }} /><b>{h.label}</b>
        <span>{health.reasons.length ? health.reasons.join(' · ') : 'просроченных и зависших задач нет'}</span>
      </div>

      <div className="dos-tiles">
        <Tile label="Открытых задач" value={health.openTasks} tone={health.overdue ? 'bad' : undefined}
          sub={health.overdue ? `просрочено: ${health.overdue}` : health.drafts ? `ждут подтверждения: ${health.drafts}` : undefined} />
        <Tile label="Действующих решений" value={data.decisions.activeTotal} sub={data.decisions.replacedTotal ? `заменено: ${data.decisions.replacedTotal}` : undefined} />
        <Tile label="Открытых вопросов" value={health.openQuestions} />
        <Tile label="Последняя встреча" value={agoLabel(health.daysSinceLast)} sub={counts.last && (health.daysSinceLast ?? 0) > 1 ? dayLabel(counts.last) : undefined}
          tone={p.status === 'active' && health.daysSinceLast != null && health.daysSinceLast > 14 ? 'warn' : undefined} />
      </div>

      <div className="dos-grid">
        <Section title="Задачи по людям" count={health.openTasks}>
          {data.tasksByPerson.length === 0 ? <Empty text="Открытых задач нет" /> : data.tasksByPerson.map(g => (
            <div key={g.owner} className="dos-group">
              <div className="dos-group-head">{g.owner} <span className="dos-count">{g.count}</span></div>
              {g.items.map(t => <TaskLine key={t.id} task={t} />)}
              {g.count > g.items.length && <div className="dos-foot">и ещё {g.count - g.items.length}</div>}
            </div>
          ))}
        </Section>
        <Section title="Действующие решения" count={data.decisions.activeTotal}>
          {data.decisions.active.length === 0 ? <Empty text="Решений пока нет" /> : data.decisions.active.map(d => <DecisionLine key={d.id} item={d} />)}
          {data.decisions.replaced.length > 0 && (
            <details className="dos-replaced"><summary>Заменённые · {data.decisions.replacedTotal}</summary>
              {data.decisions.replaced.map(d => <DecisionLine key={d.id} item={d} />)}
            </details>
          )}
        </Section>
        <Section title="Участники" count={data.people.length}>
          {data.people.length === 0 ? <Empty text="Участники не указаны" /> : data.people.map(pe => (
            <button key={pe.id} className="dos-person" onClick={() => navigate('/people/' + encodeURIComponent(pe.id))}>
              <Avatar initials={pe.initials} color={pe.color} size={28} solo />
              <span>{pe.name}{pe.role ? <span className="dos-role"> · {pe.role}</span> : null}</span>
              <span className="dos-bar-n">{plural(pe.meetings, 'встреча', 'встречи', 'встреч')}{pe.openTasks ? ` · задач: ${pe.openTasks}` : ''}</span>
            </button>
          ))}
        </Section>
        <Section title="Динамика встреч" >
          <div className="dos-weeks" role="img" aria-label="Встречи по неделям за 12 недель">
            {data.weeks.map(w => (
              <span key={w.start} title={`${dayLabel(w.start)}: ${w.meetings}`} className="dos-week">
                <span style={{ height: w.meetings ? Math.max(8, w.meetings / maxWeek * 56) : 2, background: w.meetings ? p.color : 'var(--fill2)' }} />
              </span>
            ))}
          </div>
          <div className="dos-foot">Последние 12 недель</div>
          {data.series.length > 0 && (
            <div className="dos-series">
              {data.series.map(s => (
                <button key={s.id} className="dos-line" onClick={() => navigate(recordLink(s.latestId))}>
                  <span className="dos-line-text">Серия: {s.title}</span>
                  <span className="dos-line-meta">{plural(s.count, 'встреча', 'встречи', 'встреч')}, с {dayLabel(s.first).toLowerCase()}{s.repeatedQuestions ? ' · вопрос висит несколько встреч' : ''}</span>
                </button>
              ))}
            </div>
          )}
        </Section>
        <Section title="Риски" count={data.risks.length}>
          {data.risks.length === 0 ? <Empty text="Рисков в последних записях нет" /> : data.risks.map((r, i) => <FactRow key={i} fact={r} />)}
        </Section>
        <Section title="Открытые вопросы" count={data.questions.length}>
          {data.questions.length === 0 ? <Empty text="Открытых вопросов нет" /> : data.questions.map((q, i) => <FactRow key={i} fact={q} />)}
        </Section>
        <Section title="Последние записи" wide>
          {data.recent.length === 0 ? <Empty text="Записей пока нет" /> : data.recent.map(m => <MeetingRow key={m.id} m={m} />)}
        </Section>
      </div>
    </>
  )
}
