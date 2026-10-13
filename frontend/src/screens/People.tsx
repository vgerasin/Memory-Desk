import { useState } from 'react'
import { api, errorText } from '../api'
import { navigate } from '../router'
import { useApi } from '../useApi'
import { useApp } from '../store'
import { due, plural } from '../format'
import type { PersonOverview } from '../types'
import { Avatar, DirectoryTabs, ErrorBox, Loading } from '../components/ui'
import { tr } from '../i18n'

function RoleEdit({ person }: { person: PersonOverview }) {
  const { showToast, invalidate } = useApp()
  const [editing, setEditing] = useState(false)
  const [role, setRole] = useState(person.role)
  const save = async () => {
    try { await api.patch(`/api/people/${person.id}`, { role: role.trim() }); setEditing(false); invalidate() }
    catch (e) { showToast(errorText(e)) }
  }
  if (editing) {
    return <input autoFocus value={role} onChange={e => setRole(e.target.value)} onBlur={save} aria-label={tr('Роль')}
      onKeyDown={e => { if (e.key === 'Enter') void save(); if (e.key === 'Escape') setEditing(false) }}
      style={{ height: 24, border: 'none', background: 'var(--fill)', borderRadius: 6, padding: '0 8px', fontSize: 13, width: '100%', outline: 'none' }} />
  }
  return (
    <button className="person-role" style={{ textAlign: 'left', color: person.role ? 'var(--text2)' : 'var(--text3)' }} onClick={() => setEditing(true)}>
      {person.role || tr('Указать роль')}
    </button>
  )
}

function VoiceLine({ person }: { person: PersonOverview }) {
  const { showToast, invalidate } = useApp()
  const [confirm, setConfirm] = useState(false)
  if (!person.voice) {
    return <div className="person-voice"><span className="none">{tr('Голос не запомнен — добавьте его из записи, панель «Говорящие»')}</span></div>
  }
  const minutes = Math.max(1, Math.round(person.voice.seconds / 60))
  const remove = async () => {
    try { await api.del(`/api/people/${person.id}/voice`); showToast(tr('Голосовой отпечаток удалён')); invalidate() }
    catch (e) { showToast(errorText(e)) }
    setConfirm(false)
  }
  return (
    <div className="person-voice">
      <span>{tr('Голос запомнен · {0}, ~{1} мин речи', [plural(person.voice.samples, 'запись', 'записи', 'записей'), minutes])}</span>
      {confirm
        ? <span style={{ display: 'flex', gap: 6 }}>
            <button className="btn s28 muted" onClick={() => setConfirm(false)}>{tr('Нет')}</button>
            <button className="btn s28 danger" onClick={() => void remove()}>{tr('Удалить')}</button>
          </span>
        : <button className="btn s28" onClick={() => setConfirm(true)}>{tr('Удалить голос')}</button>}
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
      invalidate(); showToast(made.created ? tr('Карточка создана') : tr('Такой человек уже есть: карточку дополнили')); onClose()
      navigate('/people/' + encodeURIComponent(made.id))
    } catch (e) { showToast(errorText(e)) } finally { setBusy(false) }
  }
  const submit = (e: { key: string }) => { if (e.key === 'Enter') void create(); if (e.key === 'Escape') onClose() }
  return (
    <div className="inline-form wrap">
      <input autoFocus value={name} onChange={e => setName(e.target.value)} onKeyDown={submit} placeholder={tr('Имя и фамилия')} aria-label={tr('Имя')} maxLength={120} />
      <input value={role} onChange={e => setRole(e.target.value)} onKeyDown={submit} placeholder={tr('Должность')} aria-label={tr('Должность')} maxLength={200} />
      <input value={organization} onChange={e => setOrganization(e.target.value)} onKeyDown={submit} placeholder={tr('Организация или отдел')} aria-label={tr('Организация')} maxLength={200} />
      <button className="btn accent s34" disabled={busy || !name.trim()} onClick={() => void create()}>{tr('Создать')}</button>
      <button className="btn s34" onClick={onClose}>{tr('Отмена')}</button>
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
        <div className="titles"><DirectoryTabs current="people" /><h1 className="h1">{tr('Люди')}</h1></div>
        {!adding && <button className="btn s34" onClick={() => setAdding(true)}>{tr('Добавить человека')}</button>}
      </div>
      {adding && <AddPerson onClose={() => setAdding(false)} />}
      {error && !data && <ErrorBox text={error} />}
      {!data && !error && <Loading />}
      {data && data.items.length === 0 && <div className="empty" style={{ marginTop: 36 }}>{tr('Люди появятся здесь, когда вы добавите их к записям или заведёте карточку кнопкой «Добавить человека».')}</div>}
      <div className="cards people">
        {data?.items.map(p => {
          const stats = [plural(p.meetings, 'встреча', 'встречи', 'встреч'), p.projects.join(', ') || tr('без проекта'),
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
                <span className="label-caps">{tr('Последняя договорённость')}</span>
                <span style={{ fontSize: 15, lineHeight: 1.45 }}>
                  {p.lastAgreement ? `${p.lastAgreement.text} — ${due(p.lastAgreement.due, p.lastAgreement.dueText).label}` : tr('Открытых договорённостей нет')}
                </span>
              </div>
              <VoiceLine person={p} />
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button className="btn accent s32" onClick={() => navigate('/people/' + encodeURIComponent(p.id))}>{tr('Досье')}</button>
                <button className="btn s32" style={{ color: 'var(--accent)' }}
                  onClick={() => chat.openChat({ kind: 'person', id: p.id, label: tr('Человек: ') + first }, tr('Какие задачи и обещания у {0}?', [p.name]))}>{tr('Что обещал(а)?')}</button>
                <button className="btn s32" onClick={() => setMaterial({ kind: 'deck', personId: p.id, title: p.name })}>{tr('Презентация')}</button>
                <button className="btn s32" onClick={() => setMaterial({ kind: 'map', personId: p.id, title: p.name })}>{tr('Карта')}</button>
              </div>
            </div>
          )
        })}
      </div>
    </>
  )
}
