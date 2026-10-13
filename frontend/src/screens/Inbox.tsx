import { useEffect, useState } from 'react'
import { api, errorText } from '../api'
import { navigate, recordLink } from '../router'
import { useApi } from '../useApi'
import { useApp } from '../store'
import { useActions } from '../actions'
import { clockOf, dayLabel, etaLabel, fmt } from '../format'
import type { Job, MeetingsResponse, TemplateDto } from '../types'
import { RecordMenus } from '../components/RecordMenus'

const STATUS = {
  running: { label: 'Обработка', bg: 'color-mix(in srgb, var(--accent) 12%, transparent)', color: 'var(--accent)' },
  queued: { label: 'В очереди', bg: 'var(--fill)', color: 'var(--text2)' },
  failed: { label: 'Ошибка', bg: 'color-mix(in srgb, #FF3B30 12%, transparent)', color: '#E0352B' },
}

function useNow(active: boolean) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!active) return
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [active])
  return now
}

function JobCard({ job, now, templates }: { job: Job; now: number; templates: TemplateDto[] }) {
  const { showToast, invalidate } = useApp()
  const [log, setLog] = useState<string | null>(null)
  const running = job.state === 'running', failed = job.state === 'failed'
  const st = STATUS[job.state]
  const started = job.started_at ? new Date(job.started_at).getTime() : null
  const elapsed = started ? Math.max(0, Math.round((now - started) / 1000)) : null
  const stageIdx = job.stage_index - 1

  const call = async (path: string, body: object, fallback?: string) => {
    try {
      const r = await api.post<{ message?: string }>(path, body)
      if (r.message || fallback) showToast(r.message || fallback!)
      invalidate()
    } catch (e) { showToast(errorText(e)) }
  }
  const toggleLog = async () => {
    if (log !== null) { setLog(null); return }
    try {
      const r = await api.get<{ events: { created_at?: string; stage?: string; detail?: string; event?: string }[]; tail: string }>(
        `/api/jobs/log?file=${encodeURIComponent(job.file)}`)
      const lines = r.events.slice(-40).map(e => `${(e.created_at || '').slice(11, 19)}  ${e.stage || e.event || ''}  ${e.detail || ''}`)
      setLog((lines.join('\n') + (r.tail ? '\n\n' + r.tail : '')).trim() || 'Журнал пока пуст')
    } catch (e) { showToast(errorText(e)) }
  }

  const sub = job.duration != null ? fmt(job.duration) : ''
  const subLine = [sub, running && started ? 'начато в ' + clockOf(job.started_at) : failed ? 'не обработан' : 'ждёт свободного исполнителя'].filter(Boolean).join(' · ')

  return (
    <div className="card r20 job">
      <div className="job-head">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
          <div className="job-file mono">{job.file}</div>
          <div style={{ fontSize: 13, color: 'var(--text2)' }}>{subLine}</div>
        </div>
        <div className="job-side">
          <span className="chip" style={{ background: st.bg, color: st.color }}>{st.label}</span>
          {running && <>
            <button className="btn s28" onClick={toggleLog}>Журнал</button>
            <button className="btn s28 danger" onClick={() => call('/api/jobs/cancel', {})}>Отменить</button>
          </>}
          {failed && <>
            {job.error?.action !== 'formats' && <button className="btn s28 ghost-accent" onClick={() => call('/api/jobs/retry', { file: job.file })}>Повторить</button>}
            <button className="btn s28" onClick={toggleLog}>Журнал</button>
            <button className="btn s28 muted" onClick={() => call('/api/jobs/dismiss', { file: job.file })}>Убрать</button>
          </>}
          {job.state === 'queued' && templates.length > 1 && (
            <select className="job-template" aria-label="Шаблон заметки" value={job.templateId ?? 'meeting'}
              onChange={e => void call('/api/jobs/template', { file: job.file, templateId: e.target.value })}>
              {templates.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          )}
          {job.state === 'queued' && <button className="btn s28 muted" onClick={() => call('/api/jobs/dismiss', { file: job.file })}>Убрать</button>}
        </div>
      </div>

      {running && (
        <div className="job-body">
          <div className="steps">{Array.from({ length: 12 }, (_, i) => <i key={i} className={i < stageIdx ? 'done' : i === stageIdx ? 'cur' : ''} />)}</div>
          <div className="job-line">
            <span style={{ whiteSpace: 'nowrap' }}><b style={{ fontWeight: 600 }}>{job.stage_label}</b><span style={{ color: 'var(--text2)' }}> · этап {job.stage_index} из 12</span></span>
            <span className="num" style={{ fontWeight: 600, color: 'var(--accent)' }}>{Math.floor(job.percent)}%</span>
          </div>
          <div className="job-stats">
            <div><span className="k">Начато</span><span className="v">{clockOf(job.started_at) || '—'}</span></div>
            <div><span className="k">Прошло</span><span className="v">{elapsed != null ? fmt(elapsed) : '—'}</span></div>
            <div><span className="k">Осталось</span><span className="v">{etaLabel(job.eta)}</span></div>
            <div><span className="k">Исполнитель</span><span className="v">{job.runner}</span></div>
          </div>
          <div className="job-msg mono">› {job.last_message || job.stage_label + '…'}</div>
        </div>
      )}

      {failed && job.error && (
        <div className="job-error">
          <span className="why">
            <span><b>{job.error.title}</b> — на этапе «{job.error.stage}»</span>
            <span className="err-detail">{job.error.detail}</span>
          </span>
          {job.error.action === 'formats' && <button className="fix" onClick={() => showToast('Поддерживаются: MP3, M4A, WAV, AAC, OGG, FLAC, MP4')}>Допустимые форматы</button>}
          {job.error.action === 'retry' && <button className="fix" onClick={() => call('/api/jobs/retry', { file: job.file })}>Повторить</button>}
        </div>
      )}
      {log !== null && <pre className="log-box mono">{log}</pre>}
    </div>
  )
}

export function Inbox() {
  const { state } = useApp()
  const now = useNow(!!state.running)
  const actions = useActions()
  const { data: reviews } = useApi<MeetingsResponse>('/api/meetings?status=needs_review')
  const { data: settings } = useApi<{ recordings_dir: string; auto_process: boolean }>('/api/settings')
  const { data: tpl } = useApi<{ items: TemplateDto[] }>('/api/templates')
  const folder = settings?.recordings_dir?.replace(/^\/Users\/[^/]+/, '~') ?? ''
  const list = reviews?.items ?? []

  return (
    <>
      <div className="page-head">
        <div className="titles">
          <h1 className="h1">Входящие</h1>
          {settings && <div className="subtitle">Папка {folder} · автоматическое обнаружение {settings.auto_process ? 'включено' : 'выключено'}</div>}
        </div>
      </div>

      <h3 className="h3" style={{ margin: '40px 0 14px' }}>Очередь обработки</h3>
      <div className="jobs">
        {state.queue.length === 0 && <div className="empty">Новых файлов нет. Положите запись в папку или нажмите «Обработать новые».</div>}
        {state.queue.map(job => <JobCard key={job.id} job={job} now={now} templates={tpl?.items ?? []} />)}
      </div>

      <h3 className="h3" style={{ margin: '44px 0 14px' }}>Требуют проверки</h3>
      <div className="jobs">
        {reviews && list.length === 0 && <div className="empty">Всё опубликовано в Obsidian.</div>}
        {list.map(r => (
          <div className="card r20 review" key={r.id}>
            <div className="review-top">
              <div className="review-main">
                <div style={{ fontSize: 13, color: 'var(--text2)' }}>{dayLabel(r.date)}, {r.time}{r.durationSec != null ? ' · ' + fmt(r.durationSec) : ''}</div>
                <div className="h3">{r.title}</div>
                {r.excerpt && <div className="review-excerpt">{r.excerpt}</div>}
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn s34" onClick={() => navigate(recordLink(r.id))}>Просмотреть</button>
                <button className="btn accent s34" onClick={() => actions.publish(r.id)}>Опубликовать в Obsidian</button>
              </div>
            </div>
            <div className="review-menus">
              <RecordMenus meeting={r} keyed idPrefix={'rv' + r.id} />
            </div>
          </div>
        ))}
      </div>
    </>
  )
}
