import { useState } from 'react'
import { api, errorText } from '../api'
import { navigate, recordLink } from '../router'
import { useApi } from '../useApi'
import { useApp } from '../store'
import { dayLabel } from '../format'
import type { ProjectOverview } from '../types'
import { AvatarStack, DirectoryTabs, ErrorBox, Loading } from '../components/ui'
import { lc, tr } from '../i18n'

export function Projects() {
  const { data, error } = useApi<{ items: ProjectOverview[] }>('/api/projects')
  const { setPrep, setMaterial, showToast, invalidate } = useApp()
  const [adding, setAdding] = useState(false)
  const [title, setTitle] = useState('')

  const create = async () => {
    const name = title.trim()
    if (!name) return
    try { await api.post('/api/projects', { title: name }); setTitle(''); setAdding(false); invalidate(); showToast(tr('Проект создан')) }
    catch (e) { showToast(errorText(e)) }
  }

  return (
    <>
      <div className="page-head">
        <div className="titles"><DirectoryTabs current="projects" /><h1 className="h1">{tr('Проекты')}</h1></div>
        {!adding && (
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn s34" onClick={() => setMaterial({ kind: 'digest', title: tr('Все проекты') })}>{tr('Дайджест')}</button>
            <button className="btn s34" onClick={() => setAdding(true)}>{tr('Новый проект')}</button>
          </div>
        )}
      </div>
      {adding && (
        <div className="inline-form">
          <input autoFocus value={title} onChange={e => setTitle(e.target.value)} placeholder={tr('Название проекта')} aria-label={tr('Название проекта')}
            onKeyDown={e => { if (e.key === 'Enter') void create(); if (e.key === 'Escape') setAdding(false) }} />
          <button className="btn accent s34" onClick={create}>{tr('Создать')}</button>
          <button className="btn s34" onClick={() => { setAdding(false); setTitle('') }}>{tr('Отмена')}</button>
        </div>
      )}
      {error && !data && <ErrorBox text={error} />}
      {!data && !error && <Loading />}
      {data && data.items.length === 0 && <div className="empty" style={{ marginTop: 36 }}>{tr('Проектов пока нет. Создайте первый — и назначайте его записям.')}</div>}
      <div className="cards projects">
        {data?.items.map(p => (
          <div className="card project-card" key={p.id}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <button className="project-name link" onClick={() => navigate('/projects/' + encodeURIComponent(p.id))}><i style={{ background: p.color }} /><span>{p.title}</span>
                {p.status && p.status !== 'active' && <em className="status-chip">{p.status === 'paused' ? tr('на паузе') : tr('завершён')}</em>}</button>
              {p.goal && <div style={{ fontSize: 15, color: 'var(--text2)', lineHeight: 1.5 }}>{p.goal}</div>}
            </div>
            <div className="stats3">
              <div><span className="v">{p.counts.records}</span><span className="k">{tr('записей')}</span></div>
              <div><span className="v">{p.counts.openTasks}</span><span className="k">{tr('открытых задач')}</span></div>
              <div><span className="v">{p.counts.questions}</span><span className="k">{tr('вопросов')}</span></div>
            </div>
            <div className="lastchange">
              <span className="label-caps">{tr('С последней встречи')}</span>
              {p.lastChange
                ? <button style={{ textAlign: 'left', fontSize: 15, lineHeight: 1.5 }} onClick={() => navigate(recordLink(p.lastChange!.meetingId))}>
                    {p.lastChange.text} — {lc(dayLabel(p.lastChange.date))}
                  </button>
                : <span style={{ fontSize: 15 }}>{tr('Пока нет встреч')}</span>}
            </div>
            <div className="card-foot">
              <AvatarStack people={p.people} size={28} />
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button className="btn accent s34" onClick={() => navigate('/projects/' + encodeURIComponent(p.id))}>{tr('Досье')}</button>
                <button className="btn s34" onClick={() => navigate('/records?project=' + encodeURIComponent(p.title))}>{tr('Записи')}</button>
                <button className="btn s34" onClick={() => setMaterial({ kind: 'map', projectId: p.id, title: p.title })}>{tr('Карта')}</button>
                <button className="btn s34" onClick={() => setPrep({ projectId: p.id, title: p.title })}>{tr('Подготовка к встрече')}</button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </>
  )
}
