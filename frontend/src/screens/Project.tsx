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
import { ChangesSection, GistCard, HangingSection, PinsSection, Pinnable } from '../components/Live'
import type { PinContext } from '../components/Live'
import { lc, tr } from '../i18n'

const STATUSES: { key: ProjectStatus; label: string }[] = [
  { key: 'active', label: tr('В работе') }, { key: 'paused', label: tr('На паузе') }, { key: 'done', label: tr('Завершён') },
]
const HEALTH = { ok: { label: tr('Всё спокойно'), color: '#34C759' }, warn: { label: tr('Требует внимания'), color: '#FF9F0A' }, bad: { label: tr('Есть проблемы'), color: '#FF3B30' } }

export function Project() {
  const { segments } = useRoute()
  const id = decodeURIComponent(segments[1] ?? '')
  const { data, error } = useApi<ProjectDossier>(id ? `/api/projects/${encodeURIComponent(id)}` : null)
  const { setPrep, setMaterial, showToast, invalidate } = useApp()
  const [goal, setGoal] = useState<string | null>(null)
  useEffect(() => { setGoal(null) }, [id])

  if (error && !data) return <><BackLink to="/projects" label={tr('Проекты')} /><ErrorBox text={error} /></>
  if (!data) return <><BackLink to="/projects" label={tr('Проекты')} /><Loading /></>
  const { project: p, health, counts } = data
  const patch = async (body: { status?: ProjectStatus; goal?: string }) => {
    try { await api.patch(`/api/projects/${encodeURIComponent(p.id)}`, body); invalidate() }
    catch (e) { showToast(errorText(e)) }
  }
  const h = HEALTH[health.level]
  const ctx: PinContext = { scope: 'project', ownerId: p.id, pins: data.pins }
  const maxWeek = Math.max(1, ...data.weeks.map(w => w.meetings))
  return (
    <>
      <BackLink to="/projects" label={tr('Проекты')} />
      <div className="dos-head card">
        <span className="dos-dot" style={{ background: p.color }} />
        <div className="dos-head-main">
          <h1 className="h1" style={{ fontSize: 30 }}>{p.title}</h1>
          <div className="dos-sub">
            <select className="dos-status" value={p.status} aria-label={tr('Статус проекта')} onChange={e => void patch({ status: e.target.value as ProjectStatus })}>
              {STATUSES.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select>
            {counts.records > 0 && <span> · {plural(counts.records, 'запись', 'записи', 'записей')}{counts.first ? tr(', с {0}', [lc(dayLabel(counts.first))]) : ''}</span>}
          </div>
        </div>
        <div className="dos-head-actions">
          <button className="btn s34" onClick={() => navigate('/records?project=' + encodeURIComponent(p.title))}>{tr('Записи')}</button>
          <button className="btn s34" onClick={() => setMaterial({ kind: 'deck', projectId: p.id, title: p.title })}>{tr('Презентация')}</button>
          <button className="btn s34" onClick={() => setMaterial({ kind: 'map', projectId: p.id, title: p.title })}>{tr('Карта')}</button>
          <DocMenu scope={{ projectId: p.id, title: p.title }} />
          <button className="btn s34" onClick={() => setMaterial({ kind: 'digest', projectId: p.id, title: p.title })}>{tr('Дайджест')}</button>
          <button className="btn accent s34" onClick={() => setPrep({ projectId: p.id, title: p.title })}>{tr('Подготовка к встрече')}</button>
        </div>
      </div>

      <div className="dos-goal card r20">
        <span className="label-caps">{tr('Цель')}</span>
        {goal === null
          ? <button className="dos-goal-text" onClick={() => setGoal(p.goal)}>{p.goal || <span style={{ color: 'var(--text3)' }}>{tr('Цель не указана — нажмите, чтобы написать')}</span>}</button>
          : <div className="dos-editor" style={{ padding: 0, boxShadow: 'none' }}>
              <textarea autoFocus value={goal} rows={3} maxLength={2000} onChange={e => setGoal(e.target.value)} aria-label={tr('Цель проекта')} />
              <div className="dos-editor-actions">
                <button className="btn accent s34" onClick={async () => { await patch({ goal }); setGoal(null) }}>{tr('Сохранить')}</button>
                <button className="btn s34" onClick={() => setGoal(null)}>{tr('Отмена')}</button>
                <span className="hint" style={{ marginLeft: 'auto' }}>{tr('Пишется в файл проекта в Obsidian')}</span>
              </div>
            </div>}
      </div>

      <div className="dos-health" style={{ borderColor: h.color }}>
        <i style={{ background: h.color }} /><b>{h.label}</b>
        <span>{health.reasons.length ? health.reasons.join(' · ') : tr('просроченных и зависших задач нет')}</span>
      </div>

      <GistCard scope="project" ownerId={p.id} gist={data.gist} />

      <div className="dos-tiles">
        <Tile label={tr('Открытых задач')} value={health.openTasks} tone={health.overdue ? 'bad' : undefined}
          sub={health.overdue ? tr('просрочено: {0}', [health.overdue]) : health.drafts ? tr('ждут подтверждения: {0}', [health.drafts]) : undefined} />
        <Tile label={tr('Действующих решений')} value={data.decisions.activeTotal} sub={data.decisions.replacedTotal ? tr('заменено: {0}', [data.decisions.replacedTotal]) : undefined} />
        <Tile label={tr('Открытых вопросов')} value={health.openQuestions} />
        <Tile label={tr('Последняя встреча')} value={agoLabel(health.daysSinceLast)} sub={counts.last && (health.daysSinceLast ?? 0) > 1 ? dayLabel(counts.last) : undefined}
          tone={p.status === 'active' && health.daysSinceLast != null && health.daysSinceLast > 14 ? 'warn' : undefined} />
      </div>

      <div className="dos-grid">
        <PinsSection scope="project" ownerId={p.id} pins={data.pins} />
        <HangingSection ctx={ctx} hanging={data.hanging} />
        <ChangesSection changes={data.changes} />
        <Section title={tr('Задачи по людям')} count={health.openTasks}>
          {data.tasksByPerson.length === 0 ? <Empty text={tr('Открытых задач нет')} /> : data.tasksByPerson.map(g => (
            <div key={g.owner} className="dos-group">
              <div className="dos-group-head">{g.owner} <span className="dos-count">{g.count}</span></div>
              {g.items.map(t => (
                <Pinnable key={t.id} ctx={ctx} payload={{ text: t.text, kind: 'task', meetingId: t.meetingId, sec: t.tSec }}><TaskLine task={t} /></Pinnable>
              ))}
              {g.count > g.items.length && <div className="dos-foot">{tr('и ещё {0}', [g.count - g.items.length])}</div>}
            </div>
          ))}
        </Section>
        <Section title={tr('Действующие решения')} count={data.decisions.activeTotal}>
          {data.decisions.active.length === 0 ? <Empty text={tr('Решений пока нет')} /> : data.decisions.active.map(d => (
            <Pinnable key={d.id} ctx={ctx} payload={{ text: d.text, kind: 'decision', meetingId: d.meetingId, sec: d.tSec }}><DecisionLine item={d} /></Pinnable>
          ))}
          {data.decisions.replaced.length > 0 && (
            <details className="dos-replaced"><summary>{tr('Заменённые · {0}', [data.decisions.replacedTotal])}</summary>
              {data.decisions.replaced.map(d => <DecisionLine key={d.id} item={d} />)}
            </details>
          )}
        </Section>
        <Section title={tr('Участники')} count={data.people.length}>
          {data.people.length === 0 ? <Empty text={tr('Участники не указаны')} /> : data.people.map(pe => (
            <button key={pe.id} className="dos-person" onClick={() => navigate('/people/' + encodeURIComponent(pe.id))}>
              <Avatar initials={pe.initials} color={pe.color} size={28} solo />
              <span>{pe.name}{pe.role ? <span className="dos-role"> · {pe.role}</span> : null}</span>
              <span className="dos-bar-n">{plural(pe.meetings, 'встреча', 'встречи', 'встреч')}{pe.openTasks ? tr(' · задач: {0}', [pe.openTasks]) : ''}</span>
            </button>
          ))}
        </Section>
        <Section title={tr('Динамика встреч')} >
          <div className="dos-weeks" role="img" aria-label={tr('Встречи по неделям за 12 недель')}>
            {data.weeks.map(w => (
              <span key={w.start} title={`${dayLabel(w.start)}: ${w.meetings}`} className="dos-week">
                <span style={{ height: w.meetings ? Math.max(8, w.meetings / maxWeek * 56) : 2, background: w.meetings ? p.color : 'var(--fill2)' }} />
              </span>
            ))}
          </div>
          <div className="dos-foot">{tr('Последние 12 недель')}</div>
          {data.series.length > 0 && (
            <div className="dos-series">
              {data.series.map(s => (
                <button key={s.id} className="dos-line" onClick={() => navigate(recordLink(s.latestId))}>
                  <span className="dos-line-text">{tr('Серия: {0}', [s.title])}</span>
                  <span className="dos-line-meta">{tr('{0}, с {1}{2}', [plural(s.count, 'встреча', 'встречи', 'встреч'), lc(dayLabel(s.first)), s.repeatedQuestions ? tr(' · вопрос висит несколько встреч') : ''])}</span>
                </button>
              ))}
            </div>
          )}
        </Section>
        <Section title={tr('Риски')} count={data.risks.length}>
          {data.risks.length === 0 ? <Empty text={tr('Рисков в последних записях нет')} /> : data.risks.map((r, i) => (
            <Pinnable key={i} ctx={ctx} payload={{ text: r.text, kind: 'risk', meetingId: r.meetingId, sec: r.sec }}><FactRow fact={r} /></Pinnable>
          ))}
        </Section>
        <Section title={tr('Открытые вопросы')} count={data.questions.length}>
          {data.questions.length === 0 ? <Empty text={tr('Открытых вопросов нет')} /> : data.questions.map((q, i) => (
            <Pinnable key={i} ctx={ctx} payload={{ text: q.text, kind: 'question', meetingId: q.meetingId, sec: q.sec }}><FactRow fact={q} /></Pinnable>
          ))}
        </Section>
        <Section title={tr('Последние записи')} wide>
          {data.recent.length === 0 ? <Empty text={tr('Записей пока нет')} /> : data.recent.map(m => <MeetingRow key={m.id} m={m} />)}
        </Section>
      </div>
    </>
  )
}
