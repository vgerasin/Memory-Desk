import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { useActions } from '../actions'
import { useApp } from '../store'
import type { DiarizationStatus, MeetingDetail, SpeakerInfo, VoiceState } from '../types'
import { IconCheck } from './ui'
import { tr } from '../i18n'

const PALETTE = ['#0A84FF', '#FF9F0A', '#34C759', '#AF52DE', '#FF375F', '#30B0C7', '#8E8E93']

export function speakerColor(label: string, m: MeetingDetail): string {
  const person = m.people.find(p => p.name.toLowerCase() === label.toLowerCase())
  if (person) return person.color
  const n = /^Спикер (\d+)$/.exec(label)
  if (n) return PALETTE[(Number(n[1]) - 1) % PALETTE.length]
  let h = 0
  for (const ch of label) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return PALETTE[h % PALETTE.length]
}

export interface PickerTarget {
  left: number; top: number; bottom: number
  /** текущая метка («Спикер 1» или имя); пусто — у реплики нет говорящего */
  label: string
  /** номер реплики; null — меню открыто с панели говорящих и действует на всю запись */
  index: number | null
  scope: 'all' | 'one'
}

export function openPicker(el: HTMLElement, label: string, index: number | null, scope: 'all' | 'one'): PickerTarget {
  const r = el.getBoundingClientRect()
  return { left: r.left, top: r.top, bottom: r.bottom, label, index, scope }
}

/** Выбор человека для говорящего: на все реплики или на одну. Имя можно ввести — человек появится в справочнике. */
export function SpeakerPicker({ meeting, target, onClose }: { meeting: MeetingDetail; target: PickerTarget; onClose: () => void }) {
  const { meta } = useApp()
  const actions = useActions()
  const ref = useRef<HTMLDivElement>(null)
  const [query, setQuery] = useState('')
  const [scope, setScope] = useState<'all' | 'one'>(target.label ? target.scope : 'one')
  const canScope = target.index != null && !!target.label

  useEffect(() => {
    const onDown = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) onClose() }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose() } }
    document.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey, true)
    return () => { document.removeEventListener('mousedown', onDown); window.removeEventListener('keydown', onKey, true) }
  }, [onClose])

  const q = query.trim()
  const people = useMemo(
    () => (meta?.people ?? []).filter(p => !q || p.name.toLowerCase().includes(q.toLowerCase())),
    [meta, q])
  const others = useMemo(() => {
    const names = new Set((meta?.people ?? []).map(p => p.name.toLowerCase()))
    return meeting.speakers
      .filter(s => s.label !== target.label && !names.has(s.label.toLowerCase()))
      .filter(s => !q || s.label.toLowerCase().includes(q.toLowerCase()))
  }, [meeting.speakers, meta, target.label, q])
  const exact = (meta?.people ?? []).some(p => p.name.toLowerCase() === q.toLowerCase())
    || meeting.speakers.some(s => s.label.toLowerCase() === q.toLowerCase())

  const apply = async (name: string) => {
    if (name === target.label && scope === 'all') { onClose(); return }
    const body = scope === 'all' && target.label
      ? { rename: { [target.label]: name } }
      : { segments: [{ i: target.index as number, speaker: name }] }
    onClose()
    await actions.safe(() => api.put(`/api/meetings/${meeting.id}/speakers`, body), name ? tr('Говорящий назначен') : tr('Метка убрана'))
  }

  const width = 300
  const left = Math.max(12, Math.min(target.left, window.innerWidth - width - 12))
  const below = window.innerHeight - target.bottom > 380 || target.top < 380
  const style = below ? { left, top: target.bottom + 6 } : { left, bottom: window.innerHeight - target.top + 6 }
  const title = target.index == null ? target.label
    : scope === 'all' && target.label ? tr('Все реплики: {0}', [target.label]) : tr('Эта реплика')

  return (
    <div className="spk-pop" ref={ref} style={{ width, ...style }} role="dialog" aria-label={tr('Кто говорит')}>
      <div className="spk-head">{title}</div>
      {canScope && (
        <div className="seg small" role="tablist" style={{ margin: '0 4px 8px' }}>
          <button role="tab" aria-selected={scope === 'all'} className={scope === 'all' ? 'on' : ''} onClick={() => setScope('all')}>{tr('Все реплики')}</button>
          <button role="tab" aria-selected={scope === 'one'} className={scope === 'one' ? 'on' : ''} onClick={() => setScope('one')}>{tr('Только эта')}</button>
        </div>
      )}
      <input className="spk-input" autoFocus value={query} onChange={e => setQuery(e.target.value)} placeholder={tr('Имя человека')}
        aria-label={tr('Имя человека')} spellCheck={false}
        onKeyDown={e => { if (e.key === 'Enter' && q) void apply(q) }} />
      <div className="spk-list">
        {q && !exact && (
          <div className="opt" onClick={() => void apply(q)}><span className="tick" /><span>{tr('＋ Новый человек «{0}»', [q])}</span></div>
        )}
        {people.map(p => (
          <div key={p.id} className="opt" onClick={() => void apply(p.name)}>
            <span className="tick">{p.name === target.label ? <IconCheck /> : null}</span>
            <span className="dot" style={{ background: p.color }} /><span>{p.name}</span>
          </div>
        ))}
        {others.length > 0 && <div className="spk-sep">{tr('В этой записи')}</div>}
        {others.map(s => (
          <div key={s.label} className="opt" onClick={() => void apply(s.label)}>
            <span className="tick" /><span className="dot" style={{ background: speakerColor(s.label, meeting) }} /><span>{s.label}</span>
          </div>
        ))}
        {target.label && <div className="spk-sep" />}
        {target.label && <div className="opt" onClick={() => void apply('')}><span className="tick" /><span>{tr('Без говорящего')}</span></div>}
      </div>
    </div>
  )
}

/** Панель над стенограммой: говорящие записи, запуск автоматической оценки и запоминание голосов. */
export function SpeakerPanel({ meeting, onPick }: { meeting: MeetingDetail; onPick: (el: HTMLElement, label: string) => void }) {
  const { invalidate, showToast } = useApp()
  const actions = useActions()
  const [status, setStatus] = useState<DiarizationStatus | null>(meeting.diarization)
  const [confirm, setConfirm] = useState<'text' | 'voice' | null>(null)
  const [remember, setRemember] = useState<SpeakerInfo | null>(null)
  const [voice, setVoice] = useState<VoiceState | null>(null)
  const running = status?.state === 'running'
  const named = meeting.speakers.some(s => !s.generic)
  const id = meeting.id

  useEffect(() => { setStatus(meeting.diarization); setConfirm(null); setRemember(null) }, [id]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api.get<VoiceState>('/api/voice').then(setVoice).catch(() => setVoice(null)) }, [id, running])
  useEffect(() => {
    if (!running) return
    let alive = true
    const tick = async () => {
      try {
        const next = await api.get<DiarizationStatus>(`/api/meetings/${id}/diarize`)
        if (!alive) return
        setStatus(next)
        if (next.state !== 'running') {
          invalidate()
          if (next.message) showToast(next.message)
        }
      } catch { /* повторим на следующем такте */ }
    }
    const timer = window.setInterval(tick, 1500)
    return () => { alive = false; clearInterval(timer) }
  }, [running, id, invalidate, showToast])

  const start = async (mode: 'text' | 'voice') => {
    if (named && confirm !== mode) { setConfirm(mode); window.setTimeout(() => setConfirm(null), 5000); return }
    setConfirm(null)
    try { setStatus(await api.post<DiarizationStatus>(`/api/meetings/${id}/diarize?mode=${mode}`)) }
    catch (e) { showToast(e instanceof Error ? e.message : tr('Не удалось запустить')) }
  }

  const saveVoice = async (s: SpeakerInfo) => {
    if (!s.personId) return
    setRemember(null)
    await actions.safe(() => api.post(`/api/people/${s.personId}/voice`, { meetingId: id, label: s.label, consent: true }), tr('Голос запомнен: {0}', [s.label]))
  }

  const progress = running && status?.total ? tr(' · {0} из {1}', [status.done ?? 0, status.total]) : ''
  const failed = status?.state === 'error'
  const byVoice = running && status?.mode === 'voice'
  const canVoice = !!voice?.available
  const teachable = meeting.speakers.filter(s => !s.generic && s.personId && s.voice && !s.voice.matched)
  const label = (mode: 'text' | 'voice', idle: string) =>
    running ? (status?.mode === mode || (!status?.mode && mode === 'text') ? tr('Определяю…') + progress : idle)
      : confirm === mode ? tr('Заменить разметку?') : idle
  return (
    <div className="spk-panel">
      <div className="spk-row">
        <span className="label-caps" style={{ margin: 0 }}>{tr('Говорящие')}</span>
        {meeting.speakers.length === 0 && !running && <span className="spk-empty">{tr('Пока не определены')}</span>}
        {meeting.speakers.map(s => (
          <button key={s.label} className="spk-chip" onClick={e => onPick(e.currentTarget, s.label)}
            title={s.voice?.matched ? tr('Узнан по голосу{0}', [s.voice.score ? tr(' (сходство {0}%)', [Math.round(s.voice.score * 100)]) : '']) : s.generic ? tr('Назначить человека') : tr('Изменить')}>
            <i style={{ background: speakerColor(s.label, meeting) }} />{s.label}<span className="n">{s.count}</span>
            {s.voice?.matched && <span className="rec">🎙</span>}
          </button>
        ))}
        <span className="spk-spacer" />
        {canVoice && (
          <button className={'btn s28' + (confirm === 'voice' ? ' danger' : '')} disabled={running} onClick={() => void start('voice')}
            title={tr('Делит запись по звуку голосов и узнаёт знакомых людей')}>
            {label('voice', tr('По голосу'))}
          </button>
        )}
        <button className={'btn s28' + (confirm === 'text' ? ' danger' : '')} disabled={running} onClick={() => void start('text')}
          title={tr('Локальная модель оценивает по тексту, кто говорит')}>
          {byVoice ? tr('По тексту') : label('text', canVoice ? tr('По тексту') : meeting.speakers.length ? tr('Определить заново') : tr('Определить говорящих'))}
        </button>
      </div>
      {failed && <div className="spk-note bad">{status?.message}</div>}
      {meeting.speakers.length > 0 && !running && (
        <div className="spk-note">
          {meeting.speakers.some(s => s.voice)
            ? tr('Говорящие определены по голосу: нажмите на имя, чтобы назначить человека. Назначенный голос можно запомнить, и в следующих записях он будет узнан сам.')
            : tr('Говорящие оцениваются по тексту и могут быть неточными: нажмите на имя, чтобы назначить человека.')}
        </div>
      )}
      {!running && teachable.length > 0 && (
        <div className="spk-voices">
          {teachable.map(s => (
            <span key={s.label}>
              {remember?.label === s.label
                ? null
                : <button className="btn link s28" onClick={() => setRemember(s)}>{tr('Запомнить голос: {0}', [s.label])}</button>}
            </span>
          ))}
          {remember && (
            <>
              <span className="consent">{tr('Сохранить голос «{0}» на этом компьютере, чтобы узнавать его в других записях? Это биометрические данные: сохраняйте только с согласия человека. Удалить отпечаток можно в разделе «Люди».', [remember.label])}</span>
              <button className="btn s28 muted" onClick={() => setRemember(null)}>{tr('Нет')}</button>
              <button className="btn s28 accent" onClick={() => void saveVoice(remember)}>{tr('Да, запомнить')}</button>
            </>
          )}
        </div>
      )}
    </div>
  )
}
