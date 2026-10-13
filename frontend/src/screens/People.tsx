import { useState } from 'react'
import { api, errorText } from '../api'
import { navigate } from '../router'
import { useApi } from '../useApi'
import { useApp } from '../store'
import { due, plural } from '../format'
import type { PersonOverview } from '../types'
import { Avatar, DirectoryTabs, ErrorBox, Loading } from '../components/ui'

function RoleEdit({ person }: { person: PersonOverview }) {
  const { showToast, invalidate } = useApp()
  const [editing, setEditing] = useState(false)
  const [role, setRole] = useState(person.role)
  const save = async () => {
    try { await api.patch(`/api/people/${person.id}`, { role: role.trim() }); setEditing(false); invalidate() }
    catch (e) { showToast(errorText(e)) }
  }
  if (editing) {
    return <input autoFocus value={role} onChange={e => setRole(e.target.value)} onBlur={save} aria-label="Роль"
      onKeyDown={e => { if (e.key === 'Enter') void save(); if (e.key === 'Escape') setEditing(false) }}
      style={{ height: 24, border: 'none', background: 'var(--fill)', borderRadius: 6, padding: '0 8px', fontSize: 13, width: '100%', outline: 'none' }} />
  }
  return (
    <button className="person-role" style={{ textAlign: 'left', color: person.role ? 'var(--text2)' : 'var(--text3)' }} onClick={() => setEditing(true)}>
      {person.role || 'Указать роль'}
    </button>
  )
}

function VoiceLine({ person }: { person: PersonOverview }) {
  const { showToast, invalidate } = useApp()
  const [confirm, setConfirm] = useState(false)
  if (!person.voice) {
    return <div className="person-voice"><span className="none">Голос не запомнен — добавьте его из записи, панель «Говорящие»</span></div>
  }
  const minutes = Math.max(1, Math.round(person.voice.seconds / 60))
  const remove = async () => {
    try { await api.del(`/api/people/${person.id}/voice`); showToast('Голосовой отпечаток удалён'); invalidate() }
    catch (e) { showToast(errorText(e)) }
    setConfirm(false)
  }
  return (
    <div className="person-voice">
      <span>Голос запомнен · {plural(person.voice.samples, 'запись', 'записи', 'записей')}, ~{minutes} мин речи</span>
      {confirm
        ? <span style={{ display: 'flex', gap: 6 }}>
            <button className="btn s28 muted" onClick={() => setConfirm(false)}>Нет</button>
            <button className="btn s28 danger" onClick={() => void remove()}>Удалить</button>
          </span>
        : <button className="btn s28" onClick={() => setConfirm(true)}>Удалить голос</button>}
    </div>
  )
}

function AddPerson({ onClose }: { onClose: () => void }) {
  const { showToast, invalidate } = useApp()
  const [name, setName] = useState('')
  const [role, setRole] = useState('')
  const [organization, setOrganization] = useState('')
  const [busy, setBusy] = useState(false)
  const create = async () => {
    if (!name.trim()) return
    setBusy(true)
    try {
      const made = await api.post<{ id: string; created: boolean }>('/api/people', { name, role, organization })
      invalidate(); showToast(made.created ? 'Карточка создана' : 'Такой человек уже есть: карточку дополнили'); onClose()
      navigate('/people/' + encodeURIComponent(made.id))
    } catch (e) { showToast(errorText(e)) } finally { setBusy(false) }
  }
  const submit = (e: { key: string }) => { if (e.key === 'Enter') void create(); if (e.key === 'Escape') onClose() }
  return (
    <div className="inline-form wrap">
      <input autoFocus value={name} onChange={e => setName(e.target.value)} onKeyDown={submit} placeholder="Имя и фамилия" aria-label="Имя" maxLength={120} />
      <input value={role} onChange={e => setRole(e.target.value)} onKeyDown={submit} placeholder="Должность" aria-label="Должность" maxLength={200} />
      <input value={organization} onChange={e => setOrganization(e.target.value)} onKeyDown={submit} placeholder="Организация или отдел" aria-label="Организация" maxLength={200} />
      <button className="btn accent s34" disabled={busy || !name.trim()} onClick={() => void create()}>Создать</button>
      <button className="btn s34" onClick={onClose}>Отмена</button>
    </div>
  )
}

export function People() {
  const { data, error } = useApi<{ items: PersonOverview[] }>('/api/people')
  const { chat, setMaterial } = useApp()
  const [adding, setAdding] = useState(false)
  return (
    <>
      <div className="page-head">
        <div className="titles"><DirectoryTabs current="people" /><h1 className="h1">Люди</h1></div>
        {!adding && <button className="btn s34" onClick={() => setAdding(true)}>Добавить человека</button>}
      </div>
      {adding && <AddPerson onClose={() => setAdding(false)} />}
      {error && !data && <ErrorBox text={error} />}
      {!data && !error && <Loading />}
      {data && data.items.length === 0 && <div className="empty" style={{ marginTop: 36 }}>Люди появятся здесь, когда вы добавите их к записям или заведёте карточку кнопкой «Добавить человека».</div>}
      <div className="cards people">
        {data?.items.map(p => {
          const stats = [plural(p.meetings, 'встреча', 'встречи', 'встреч'), p.projects.join(', ') || 'без проекта',
            plural(p.obligations, 'обязательство', 'обязательства', 'обязательств')].join(' · ')
          const first = p.name.split(' ')[0]
          return (
            <div className="card person-card" key={p.id}>
              <div className="person-top">
                <Avatar initials={p.initials} color={p.color} size={48} solo />
                <div style={{ minWidth: 0, flex: 1 }}>
                  <button className="person-name link" onClick={() => navigate('/people/' + encodeURIComponent(p.id))}>{p.name}</button>
                  <RoleEdit person={p} />
                  {p.organization && <div className="person-org">{p.organization}</div>}
                </div>
              </div>
              <div style={{ fontSize: 13, color: 'var(--text2)' }}>{stats}</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span className="label-caps">Последняя договорённость</span>
                <span style={{ fontSize: 15, lineHeight: 1.45 }}>
                  {p.lastAgreement ? `${p.lastAgreement.text} — ${due(p.lastAgreement.due, p.lastAgreement.dueText).label}` : 'Открытых договорённостей нет'}
                </span>
              </div>
              <VoiceLine person={p} />
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button className="btn accent s32" onClick={() => navigate('/people/' + encodeURIComponent(p.id))}>Досье</button>
                <button className="btn s32" style={{ color: 'var(--accent)' }}
                  onClick={() => chat.openChat({ kind: 'person', id: p.id, label: 'Человек: ' + first }, `Какие задачи и обещания у ${p.name}?`)}>
                  Что обещал(а)?
                </button>
                <button className="btn s32" onClick={() => setMaterial({ kind: 'deck', personId: p.id, title: p.name })}>Презентация</button>
                <button className="btn s32" onClick={() => setMaterial({ kind: 'map', personId: p.id, title: p.name })}>Карта</button>
              </div>
            </div>
          )
        })}
      </div>
    </>
  )
}
