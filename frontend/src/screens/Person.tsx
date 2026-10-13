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
import { ChangesSection, GistCard, HangingSection, PinsSection, Pinnable } from '../components/Live'
import type { PinContext } from '../components/Live'
import { lc, tr } from '../i18n'

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
      invalidate(); showToast(tr('Карточка сохранена')); onDone()
    } catch (e) { showToast(errorText(e)) } finally { setBusy(false) }
  }
  return (
    <div className="dos-editor">
      <label>{tr('Должность')}<input value={role} onChange={e => setRole(e.target.value)} maxLength={200} placeholder={tr('Например, руководитель группы')} /></label>
      <label>{tr('Организация или отдел')}<input value={organization} onChange={e => setOrganization(e.target.value)} maxLength={200} placeholder={tr('Например, ДЦТ')} /></label>
      <label>{tr('Как ещё называют')}<input value={aliases} onChange={e => setAliases(e.target.value)} placeholder={tr('Через запятую: Саша, Александр П.')} />
        <span className="hint">{tr('По этим именам задачи, где он указан ответственным, попадают в его карточку.')}</span></label>
      <label>{tr('Заметка')}<textarea value={notes} onChange={e => setNotes(e.target.value)} rows={4} maxLength={5000} placeholder={tr('Что важно помнить о человеке')} /></label>
      <div className="dos-editor-actions">
        <button className="btn accent s34" disabled={busy} onClick={() => void save()}>{tr('Сохранить')}</button>
        <button className="btn s34" onClick={onDone}>{tr('Отмена')}</button>
        <span className="hint" style={{ marginLeft: 'auto' }}>{tr('Карточка хранится в Obsidian: Люди/{0}.md', [p.name])}</span>
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

  if (error && !data) return <><BackLink to="/people" label={tr('Люди')} /><ErrorBox text={error} /></>
  if (!data) return <><BackLink to="/people" label={tr('Люди')} /><Loading /></>
  const { person: p, stats, tasks } = data
  const first = p.name.split(' ')[0]
  const sub = [p.role, p.organization].filter(Boolean).join(' · ')
  const ctx: PinContext = { scope: 'person', ownerId: p.id, pins: data.pins }
  return (
    <>
      <BackLink to="/people" label={tr('Люди')} />
      <div className="dos-head card">
        <Avatar initials={p.initials} color={p.color} size={64} solo />
        <div className="dos-head-main">
          <h1 className="h1" style={{ fontSize: 30 }}>{p.name}</h1>
          <div className="dos-sub">{sub || <span style={{ color: 'var(--text3)' }}>{tr('Должность и организация не указаны')}</span>}</div>
          {(p.aliases ?? []).length > 0 && <div className="dos-alias">{tr('Также: {0}', [(p.aliases ?? []).join(', ')])}</div>}
        </div>
        <div className="dos-head-actions">
          <button className="btn s34" onClick={() => setEditing(v => !v)}>{editing ? tr('Закрыть') : tr('Изменить')}</button>
          <button className="btn s34" style={{ color: 'var(--accent)' }}
            onClick={() => chat.openChat({ kind: 'person', id: p.id, label: tr('Человек: ') + first }, tr('Какие задачи и обещания у {0}?', [p.name]))}>{tr('Что обещал(а)?')}</button>
          <button className="btn s34" onClick={() => setMaterial({ kind: 'deck', personId: p.id, title: p.name })}>{tr('Презентация')}</button>
          <button className="btn s34" onClick={() => setMaterial({ kind: 'map', personId: p.id, title: p.name })}>{tr('Карта')}</button>
          <DocMenu scope={{ personId: p.id, title: p.name }} />
        </div>
      </div>
      {editing && <Editor dossier={data} onDone={() => setEditing(false)} />}
      {!editing && p.notes && <div className="dos-notes card r20">{p.notes}</div>}

      <GistCard scope="person" ownerId={p.id} gist={data.gist} />

      <div className="dos-tiles">
        <Tile label={tr('Встреч')} value={stats.meetings} sub={stats.meetings ? tr('за 30 дней: {0}', [stats.meetingsLast30]) : tr('в записях не встречается')} />
        <Tile label={tr('Последний контакт')} value={agoLabel(stats.daysSince)} sub={stats.lastMeeting ? stats.lastMeeting.title : undefined}
          tone={stats.daysSince != null && stats.daysSince > 30 ? 'warn' : undefined} />
        <Tile label={tr('Открытых обязательств')} value={tasks.open} tone={tasks.overdue ? 'bad' : undefined}
          sub={tasks.overdue ? tr('просрочено: {0}', [tasks.overdue]) : tasks.done ? tr('выполнено: {0}', [tasks.done]) : undefined} />
        <Tile label={tr('Доля речи')} value={stats.talk ? Math.round(stats.talk.share * 100) + '%' : '—'}
          sub={stats.talk ? tr('в размеченных записях') : tr('нет записей с размеченными говорящими')} />
      </div>

      <div className="dos-grid">
        <PinsSection scope="person" ownerId={p.id} pins={data.pins} />
        <HangingSection ctx={ctx} hanging={data.hanging} />
        <ChangesSection changes={data.changes} />
        <Section title={tr('Обязательства')} count={tasks.open}>
          {tasks.items.length === 0 ? <Empty text={tr('Открытых обязательств нет')} /> : tasks.items.map(t => (
            <Pinnable key={t.id} ctx={ctx} payload={{ text: t.text, kind: 'task', meetingId: t.meetingId, sec: t.tSec }}><TaskLine task={t} /></Pinnable>
          ))}
          {tasks.open > tasks.items.length && <button className="link-btn" onClick={() => navigate('/tasks')}>{tr('Все задачи')}</button>}
        </Section>
        <Section title={tr('Действующие решения')} count={data.decisions.total}>
          {data.decisions.items.length === 0 ? <Empty text={tr('В записях с участием человека решений нет')} /> : data.decisions.items.map(d => (
            <Pinnable key={d.id} ctx={ctx} payload={{ text: d.text, kind: 'decision', meetingId: d.meetingId, sec: d.tSec }}><DecisionLine item={d} /></Pinnable>
          ))}
        </Section>
        <Section title={tr('Проекты')}>
          {data.projects.length === 0 ? <Empty text={tr('Проектов нет')} /> : data.projects.map(pr => (
            <button key={pr.id ?? pr.title} className="dos-bar" onClick={() => navigate(pr.id ? '/projects/' + encodeURIComponent(pr.id) : '/records?project=' + encodeURIComponent(pr.title))}>
              <span className="dos-bar-label"><i style={{ background: pr.color }} />{pr.title}</span>
              <span className="dos-bar-track"><span style={{ width: Math.max(4, pr.share * 100) + '%', background: pr.color }} /></span>
              <span className="dos-bar-n">{plural(pr.meetings, 'встреча', 'встречи', 'встреч')}</span>
            </button>
          ))}
        </Section>
        <Section title={tr('С кем чаще встречается')}>
          {data.companions.length === 0 ? <Empty text={tr('Других участников нет')} /> : data.companions.map(c => (
            <button key={c.id} className="dos-person" onClick={() => navigate('/people/' + encodeURIComponent(c.id))}>
              <Avatar initials={c.initials} color={c.color} size={28} solo />
              <span>{c.name}</span><span className="dos-bar-n">{plural(c.meetings, 'встреча', 'встречи', 'встреч')}</span>
            </button>
          ))}
        </Section>
        <Section title={tr('Открытые вопросы в последних записях')} count={data.questions.length}>
          {data.questions.length === 0 ? <Empty text={tr('Из последних записей вопросов не осталось')} /> : data.questions.map((q, i) => (
            <Pinnable key={i} ctx={ctx} payload={{ text: q.text, kind: 'question', meetingId: q.meetingId, sec: q.sec }}><FactRow fact={q} /></Pinnable>
          ))}
        </Section>
        <Section title={tr('Хронология')}>
          {data.timeline.length === 0 ? <Empty text={tr('Записей нет')} /> : data.timeline.map(m => <MeetingRow key={m.id} m={m} />)}
          {stats.firstContact && <div className="dos-foot">{tr('Первая запись: {0}', [lc(dayLabel(stats.firstContact))])}</div>}
        </Section>
      </div>
      {p.voice && <div className="dos-foot">{tr('Голос запомнен: {0}. Удалить отпечаток можно на экране «Люди».', [plural(p.voice.samples, 'запись', 'записи', 'записей')])}</div>}
    </>
  )
}
