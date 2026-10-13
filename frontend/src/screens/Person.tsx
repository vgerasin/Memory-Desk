import { useEffect, useState } from 'react'
import { api, errorText } from '../api'
import { navigate, useRoute } from '../router'
import { useApi } from '../useApi'
import { useApp } from '../store'
import { dayLabel, plural } from '../format'
import type { PersonDossier } from '../types'
import { Avatar, ErrorBox, Loading } from '../components/ui'
import { agoLabel, BackLink, DecisionLine, Empty, FactRow, MeetingRow, Section, TaskLine, Tile } from '../components/Dossier'
import { DocMenu } from '../components/DocMenu'

function Editor({ dossier, onDone }: { dossier: PersonDossier; onDone: () => void }) {
  const { showToast, invalidate } = useApp()
  const p = dossier.person
  const [role, setRole] = useState(p.role)
  const [organization, setOrganization] = useState(p.organization ?? '')
  const [aliases, setAliases] = useState((p.aliases ?? []).join(', '))
  const [notes, setNotes] = useState(p.notes ?? '')
  const [busy, setBusy] = useState(false)
  const save = async () => {
    setBusy(true)
    try {
      await api.patch(`/api/people/${p.id}`, { role, organization, aliases, notes })
      invalidate(); showToast('Карточка сохранена'); onDone()
    } catch (e) { showToast(errorText(e)) } finally { setBusy(false) }
  }
  return (
    <div className="dos-editor">
      <label>Должность<input value={role} onChange={e => setRole(e.target.value)} maxLength={200} placeholder="Например, руководитель группы" /></label>
      <label>Организация или отдел<input value={organization} onChange={e => setOrganization(e.target.value)} maxLength={200} placeholder="Например, ДЦТ" /></label>
      <label>Как ещё называют<input value={aliases} onChange={e => setAliases(e.target.value)} placeholder="Через запятую: Саша, Александр П." />
        <span className="hint">По этим именам задачи, где он указан ответственным, попадают в его карточку.</span></label>
      <label>Заметка<textarea value={notes} onChange={e => setNotes(e.target.value)} rows={4} maxLength={5000} placeholder="Что важно помнить о человеке" /></label>
      <div className="dos-editor-actions">
        <button className="btn accent s34" disabled={busy} onClick={() => void save()}>Сохранить</button>
        <button className="btn s34" onClick={onDone}>Отмена</button>
        <span className="hint" style={{ marginLeft: 'auto' }}>Карточка хранится в Obsidian: Люди/{p.name}.md</span>
      </div>
    </div>
  )
}

export function Person() {
  const { segments } = useRoute()
  const id = decodeURIComponent(segments[1] ?? '')
  const { data, error } = useApi<PersonDossier>(id ? `/api/people/${encodeURIComponent(id)}` : null)
  const { chat, setMaterial } = useApp()
  const [editing, setEditing] = useState(false)
  useEffect(() => { setEditing(false) }, [id])

  if (error && !data) return <><BackLink to="/people" label="Люди" /><ErrorBox text={error} /></>
  if (!data) return <><BackLink to="/people" label="Люди" /><Loading /></>
  const { person: p, stats, tasks } = data
  const first = p.name.split(' ')[0]
  const sub = [p.role, p.organization].filter(Boolean).join(' · ')
  return (
    <>
      <BackLink to="/people" label="Люди" />
      <div className="dos-head card">
        <Avatar initials={p.initials} color={p.color} size={64} solo />
        <div className="dos-head-main">
          <h1 className="h1" style={{ fontSize: 30 }}>{p.name}</h1>
          <div className="dos-sub">{sub || <span style={{ color: 'var(--text3)' }}>Должность и организация не указаны</span>}</div>
          {(p.aliases ?? []).length > 0 && <div className="dos-alias">Также: {(p.aliases ?? []).join(', ')}</div>}
        </div>
        <div className="dos-head-actions">
          <button className="btn s34" onClick={() => setEditing(v => !v)}>{editing ? 'Закрыть' : 'Изменить'}</button>
          <button className="btn s34" style={{ color: 'var(--accent)' }}
            onClick={() => chat.openChat({ kind: 'person', id: p.id, label: 'Человек: ' + first }, `Какие задачи и обещания у ${p.name}?`)}>Что обещал(а)?</button>
          <button className="btn s34" onClick={() => setMaterial({ kind: 'deck', personId: p.id, title: p.name })}>Презентация</button>
          <button className="btn s34" onClick={() => setMaterial({ kind: 'map', personId: p.id, title: p.name })}>Карта</button>
          <DocMenu scope={{ personId: p.id, title: p.name }} />
        </div>
      </div>
      {editing && <Editor dossier={data} onDone={() => setEditing(false)} />}
      {!editing && p.notes && <div className="dos-notes card r20">{p.notes}</div>}

      <div className="dos-tiles">
        <Tile label="Встреч" value={stats.meetings} sub={stats.meetings ? `за 30 дней: ${stats.meetingsLast30}` : 'в записях не встречается'} />
        <Tile label="Последний контакт" value={agoLabel(stats.daysSince)} sub={stats.lastMeeting ? stats.lastMeeting.title : undefined}
          tone={stats.daysSince != null && stats.daysSince > 30 ? 'warn' : undefined} />
        <Tile label="Открытых обязательств" value={tasks.open} tone={tasks.overdue ? 'bad' : undefined}
          sub={tasks.overdue ? `просрочено: ${tasks.overdue}` : tasks.done ? `выполнено: ${tasks.done}` : undefined} />
        <Tile label="Доля речи" value={stats.talk ? Math.round(stats.talk.share * 100) + '%' : '—'}
          sub={stats.talk ? 'в размеченных записях' : 'нет записей с размеченными говорящими'} />
      </div>

      <div className="dos-grid">
        <Section title="Обязательства" count={tasks.open}>
          {tasks.items.length === 0 ? <Empty text="Открытых обязательств нет" /> : tasks.items.map(t => <TaskLine key={t.id} task={t} />)}
          {tasks.open > tasks.items.length && <button className="link-btn" onClick={() => navigate('/tasks')}>Все задачи</button>}
        </Section>
        <Section title="Действующие решения" count={data.decisions.total}>
          {data.decisions.items.length === 0 ? <Empty text="В записях с участием человека решений нет" /> : data.decisions.items.map(d => <DecisionLine key={d.id} item={d} />)}
        </Section>
        <Section title="Проекты">
          {data.projects.length === 0 ? <Empty text="Проектов нет" /> : data.projects.map(pr => (
            <button key={pr.id ?? pr.title} className="dos-bar" onClick={() => navigate(pr.id ? '/projects/' + encodeURIComponent(pr.id) : '/records?project=' + encodeURIComponent(pr.title))}>
              <span className="dos-bar-label"><i style={{ background: pr.color }} />{pr.title}</span>
              <span className="dos-bar-track"><span style={{ width: Math.max(4, pr.share * 100) + '%', background: pr.color }} /></span>
              <span className="dos-bar-n">{plural(pr.meetings, 'встреча', 'встречи', 'встреч')}</span>
            </button>
          ))}
        </Section>
        <Section title="С кем чаще встречается">
          {data.companions.length === 0 ? <Empty text="Других участников нет" /> : data.companions.map(c => (
            <button key={c.id} className="dos-person" onClick={() => navigate('/people/' + encodeURIComponent(c.id))}>
              <Avatar initials={c.initials} color={c.color} size={28} solo />
              <span>{c.name}</span><span className="dos-bar-n">{plural(c.meetings, 'встреча', 'встречи', 'встреч')}</span>
            </button>
          ))}
        </Section>
        <Section title="Открытые вопросы" count={data.questions.length}>
          {data.questions.length === 0 ? <Empty text="Из последних записей вопросов не осталось" /> : data.questions.map((q, i) => <FactRow key={i} fact={q} />)}
        </Section>
        <Section title="Хронология">
          {data.timeline.length === 0 ? <Empty text="Записей нет" /> : data.timeline.map(m => <MeetingRow key={m.id} m={m} />)}
          {stats.firstContact && <div className="dos-foot">Первая запись: {dayLabel(stats.firstContact).toLowerCase()}</div>}
        </Section>
      </div>
      {p.voice && <div className="dos-foot">Голос запомнен: {plural(p.voice.samples, 'запись', 'записи', 'записей')}. Удалить отпечаток можно на экране «Люди».</div>}
    </>
  )
}
