import { useEffect, useState } from 'react'
import { api, errorText } from '../api'
import { useApi } from '../useApi'
import { useApp } from '../store'
import { clockOf, etaLabel, fmt, plural } from '../format'
import type { Job, TemplateDto } from '../types'
import { tr } from '../i18n'

const STATUS = {
  running: { label: tr('Обработка'), bg: 'color-mix(in srgb, var(--accent) 12%, transparent)', color: 'var(--accent)' },
  queued: { label: tr('В очереди'), bg: 'var(--fill)', color: 'var(--text2)' },
  failed: { label: tr('Ошибка'), bg: 'color-mix(in srgb, #FF3B30 12%, transparent)', color: '#E0352B' },
}

export function useNow(active: boolean) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!active) return
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [active])
  return now
}

export function JobCard({ job, now, templates }: { job: Job; now: number; templates: TemplateDto[] }) {
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
      setLog((lines.join('\n') + (r.tail ? '\n\n' + r.tail : '')).trim() || tr('Журнал пока пуст'))
    } catch (e) { showToast(errorText(e)) }
  }

  const sub = job.duration != null ? fmt(job.duration) : ''
  const subLine = [sub, running && started ? tr('начато в ') + clockOf(job.started_at) : failed ? tr('не обработан') : tr('ждёт свободного исполнителя')].filter(Boolean).join(' · ')

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
            <button className="btn s28" onClick={toggleLog}>{tr('Журнал')}</button>
            <button className="btn s28 danger" onClick={() => call('/api/jobs/cancel', {})}>{tr('Отменить')}</button>
          </>}
          {failed && <>
            {job.error?.action !== 'formats' && <button className="btn s28 ghost-accent" onClick={() => call('/api/jobs/retry', { file: job.file })}>{tr('Повторить')}</button>}
            <button className="btn s28" onClick={toggleLog}>{tr('Журнал')}</button>
            <button className="btn s28 muted" onClick={() => call('/api/jobs/dismiss', { file: job.file })}>{tr('Убрать')}</button>
          </>}
          {job.state === 'queued' && templates.length > 1 && (
            <select className="job-template" aria-label={tr('Шаблон заметки')} value={job.templateId ?? 'meeting'}
              onChange={e => void call('/api/jobs/template', { file: job.file, templateId: e.target.value })}>
              {templates.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          )}
          {job.state === 'queued' && <button className="btn s28 muted" onClick={() => call('/api/jobs/dismiss', { file: job.file })}>{tr('Убрать')}</button>}
        </div>
      </div>

      {running && (
        <div className="job-body">
          <div className="steps">{Array.from({ length: 12 }, (_, i) => <i key={i} className={i < stageIdx ? 'done' : i === stageIdx ? 'cur' : ''} />)}</div>
          <div className="job-line">
            <span style={{ whiteSpace: 'nowrap' }}><b style={{ fontWeight: 600 }}>{job.stage_label}</b><span style={{ color: 'var(--text2)' }}>{tr(' · этап {0} из 12', [job.stage_index])}</span></span>
            <span className="num" style={{ fontWeight: 600, color: 'var(--accent)' }}>{Math.floor(job.percent)}%</span>
          </div>
          <div className="job-stats">
            <div><span className="k">{tr('Начато')}</span><span className="v">{clockOf(job.started_at) || '—'}</span></div>
            <div><span className="k">{tr('Прошло')}</span><span className="v">{elapsed != null ? fmt(elapsed) : '—'}</span></div>
            <div><span className="k">{tr('Осталось')}</span><span className="v">{etaLabel(job.eta)}</span></div>
            <div><span className="k">{tr('Исполнитель')}</span><span className="v">{job.runner}</span></div>
          </div>
          <div className="job-msg mono">› {job.last_message || job.stage_label + '…'}</div>
        </div>
      )}

      {failed && job.error && (
        <div className="job-error">
          <span className="why">
            <span><b>{job.error.title}</b>{' '}{tr(' — на этапе «')}{job.error.stage}»</span>
            <span className="err-detail">{job.error.detail}</span>
          </span>
          {job.error.action === 'formats' && <button className="fix" onClick={() => showToast(tr('Поддерживаются: MP3, M4A, WAV, AAC, OGG, FLAC, MP4'))}>{tr('Допустимые форматы')}</button>}
          {job.error.action === 'retry' && <button className="fix" onClick={() => call('/api/jobs/retry', { file: job.file })}>{tr('Повторить')}</button>}
        </div>
      )}
      {log !== null && <pre className="log-box mono">{log}</pre>}
    </div>
  )
}


/** «Новые и в обработке»: очередь файлов с выбором шаблона, ходом работы и ошибками. Пока файлов нет, блока на экране нет. */
export function IntakePanel() {
  const { state } = useApp()
  const now = useNow(!!state.running)
  const { data: tpl } = useApi<{ items: TemplateDto[] }>('/api/templates')
  const { data: settings } = useApi<{ recordings_dir: string; auto_process: boolean }>('/api/settings')
  if (state.queue.length === 0) return null
  const folder = settings?.recordings_dir?.replace(/^\/Users\/[^/]+/, '~') ?? ''
  const waiting = state.queue.filter(j => j.state === 'queued').length
  return (
    <section className="intake" aria-label={tr('Новые и в обработке')}>
      <div className="intake-head">
        <h3 className="h3">{tr('Новые и в обработке')}</h3>
        <span className="intake-sub">
          {plural(state.queue.length, 'файл', 'файла', 'файлов')}{waiting > 0 ? tr(' · ждут запуска: {0}', [waiting]) : ''}
          {settings ? tr(' · папка {0}, автообнаружение {1}', [folder, settings.auto_process ? tr('включено') : tr('выключено')]) : ''}
        </span>
      </div>
      <div className="jobs">
        {state.queue.map(job => <JobCard key={job.id} job={job} now={now} templates={tpl?.items ?? []} />)}
      </div>
    </section>
  )
}
